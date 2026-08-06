import { PresenceHandler } from '../handlers/presence';
import { AwarenessService } from '../services/awareness';
import { FakeIO, FakeSocket } from './helpers/fake-socket';
import { createTestLogger, type TestLogger } from './helpers/test-logger';

const INTERVAL_MS = 60_000;
const MAX_IDLE_MS = 300_000;
const EPOCH = Date.UTC(2026, 0, 1, 12, 0, 0);

/** Presence reporting and the idle-session reaper, driven with a frozen clock. */
describe('PresenceHandler', () => {
  let awareness: AwarenessService;
  let presence: PresenceHandler;
  let io: FakeIO;
  let logger: TestLogger;
  let timers: NodeJS.Timeout[];

  beforeEach(() => {
    jest.useFakeTimers({ doNotFake: ['setImmediate'] });
    jest.setSystemTime(EPOCH);
    logger = createTestLogger();
    awareness = new AwarenessService(logger.asLogger());
    presence = new PresenceHandler(awareness, logger.asLogger());
    io = new FakeIO();
    timers = [];
  });

  afterEach(() => {
    timers.forEach(clearInterval);
    jest.useRealTimers();
  });

  function addClient(documentId: string, socketId: string, userId: string): FakeSocket {
    const socket = io.register(new FakeSocket(socketId));
    awareness.addUser(documentId, socketId, userId, userId, `${userId}@test`);
    return socket;
  }

  function startCleanup(onDocumentEmpty?: (documentId: string) => void): void {
    timers.push(
      presence.startCleanupInterval(
        io.asServer(),
        INTERVAL_MS,
        MAX_IDLE_MS,
        onDocumentEmpty,
      ),
    );
  }

  describe('presence reporting', () => {
    it('reports the users in a document', () => {
      addClient('doc-1', 's1', 'u1');
      addClient('doc-1', 's2', 'u2');

      const info = presence.getDocumentPresence('doc-1');

      expect(info.documentId).toBe('doc-1');
      expect(info.count).toBe(2);
      expect(info.users.map((u) => u.userId).sort()).toEqual(['u1', 'u2']);
    });

    it('reports an empty document for an unknown id', () => {
      expect(presence.getDocumentPresence('doc-unknown')).toEqual({
        documentId: 'doc-unknown',
        users: [],
        count: 0,
      });
    });

    it('lists active documents with their user counts', () => {
      addClient('doc-1', 's1', 'u1');
      addClient('doc-1', 's2', 'u2');
      addClient('doc-2', 's3', 'u3');

      expect(
        presence
          .getActiveDocuments()
          .sort((a, b) => a.documentId.localeCompare(b.documentId)),
      ).toEqual([
        { documentId: 'doc-1', userCount: 2 },
        { documentId: 'doc-2', userCount: 1 },
      ]);
    });

    it('lists nothing when no document is open', () => {
      expect(presence.getActiveDocuments()).toEqual([]);
    });

    it('broadcasts the presence payload to the document room', () => {
      addClient('doc-1', 's1', 'u1');

      presence.broadcastPresenceUpdate(io.asServer(), 'doc-1');

      expect(io.emitsOf('presence-update')).toHaveLength(1);
      expect(io.emitsOf('presence-update')[0].room).toBe('doc:doc-1');
      expect(io.emitsOf('presence-update')[0].payload).toMatchObject({ count: 1 });
    });

    it('broadcasts an empty presence payload for a document nobody is in', () => {
      presence.broadcastPresenceUpdate(io.asServer(), 'doc-gone');

      expect(io.emitsOf('presence-update')[0].payload).toMatchObject({
        count: 0,
        users: [],
      });
    });
  });

  describe('idle cleanup interval', () => {
    it.each([
      ['just before the interval', INTERVAL_MS - 1, 1],
      ['exactly at the interval', INTERVAL_MS, 0],
      ['after two intervals', INTERVAL_MS * 2, 0],
    ])('sweeps %s (%ims) leaving %i user(s)', (_label, elapsedMs, expectedRemaining) => {
      addClient('doc-1', 's1', 'u1');
      startCleanup();
      jest.setSystemTime(EPOCH + MAX_IDLE_MS + 1);

      jest.advanceTimersByTime(elapsedMs);

      expect(awareness.getDocumentUserCount('doc-1')).toBe(expectedRemaining);
    });

    it.each([
      ['limit-1', MAX_IDLE_MS - 1, 1],
      ['limit', MAX_IDLE_MS, 1],
      ['limit+1', MAX_IDLE_MS + 1, 0],
    ])(
      'a sweep that sees %s (%ims) of idleness leaves %i user(s)',
      (_label, idleMs, expectedRemaining) => {
        addClient('doc-1', 's1', 'u1');
        startCleanup();
        // advanceTimersByTime also moves the clock, so wind back by one interval to
        // make the sweep land on exactly `idleMs` of idleness.
        jest.setSystemTime(EPOCH + idleMs - INTERVAL_MS);

        jest.advanceTimersByTime(INTERVAL_MS);

        expect(awareness.getDocumentUserCount('doc-1')).toBe(expectedRemaining);
      },
    );

    it('notifies the evicted socket and the room, then broadcasts presence', () => {
      const evicted = addClient('doc-1', 's1', 'u1');
      addClient('doc-1', 's2', 'u2');
      startCleanup();
      jest.setSystemTime(EPOCH + MAX_IDLE_MS + 1);
      awareness.refreshActivity('s2');

      jest.advanceTimersByTime(INTERVAL_MS);

      expect(evicted.emitsOf('session-expired')).toEqual([
        { documentId: 'doc-1', reason: 'idle_timeout' },
      ]);
      expect(evicted.rooms.has('doc:doc-1')).toBe(false);
      expect(evicted.broadcastsOf('user-left')[0].payload).toEqual({
        socketId: 's1',
        userId: 'u1',
      });
      expect(io.emitsOf('presence-update')).toHaveLength(1);
      expect(logger.messages('info')).toContain('stale_user_removed_from_presence');
    });

    it('still broadcasts presence when the evicted socket is already gone', () => {
      addClient('doc-1', 's1', 'u1');
      io.sockets.sockets.delete('s1');
      startCleanup();
      jest.setSystemTime(EPOCH + MAX_IDLE_MS + 1);

      expect(() => jest.advanceTimersByTime(INTERVAL_MS)).not.toThrow();
      expect(io.emitsOf('presence-update')).toHaveLength(1);
      expect(awareness.getDocumentUserCount('doc-1')).toBe(0);
    });

    it('reports the document as empty exactly once when its last user is reaped', () => {
      addClient('doc-1', 's1', 'u1');
      const onDocumentEmpty = jest.fn();
      startCleanup(onDocumentEmpty);
      jest.setSystemTime(EPOCH + MAX_IDLE_MS + 1);

      jest.advanceTimersByTime(INTERVAL_MS * 3);

      expect(onDocumentEmpty).toHaveBeenCalledTimes(1);
      expect(onDocumentEmpty).toHaveBeenCalledWith('doc-1');
    });

    it('does not report a document as empty while a user remains', () => {
      addClient('doc-1', 'stale', 'u-stale');
      const onDocumentEmpty = jest.fn();
      startCleanup(onDocumentEmpty);
      jest.setSystemTime(EPOCH + MAX_IDLE_MS + 1);
      addClient('doc-1', 'fresh', 'u-fresh');

      jest.advanceTimersByTime(INTERVAL_MS);

      expect(awareness.getDocumentUserCount('doc-1')).toBe(1);
      expect(onDocumentEmpty).not.toHaveBeenCalled();
    });

    it('runs without an onDocumentEmpty callback', () => {
      addClient('doc-1', 's1', 'u1');
      startCleanup();
      jest.setSystemTime(EPOCH + MAX_IDLE_MS + 1);

      expect(() => jest.advanceTimersByTime(INTERVAL_MS)).not.toThrow();
      expect(awareness.getDocumentUserCount('doc-1')).toBe(0);
    });

    it('does not broadcast anything on a sweep that evicts nobody', () => {
      addClient('doc-1', 's1', 'u1');
      startCleanup();

      jest.advanceTimersByTime(INTERVAL_MS * 4);

      expect(io.emitsOf('presence-update')).toHaveLength(0);
    });

    it('sweeps every affected document in one tick', () => {
      addClient('doc-1', 's1', 'u1');
      addClient('doc-2', 's2', 'u2');
      const onDocumentEmpty = jest.fn();
      startCleanup(onDocumentEmpty);
      jest.setSystemTime(EPOCH + MAX_IDLE_MS + 1);

      jest.advanceTimersByTime(INTERVAL_MS);

      expect(
        io
          .emitsOf('presence-update')
          .map((e) => e.room)
          .sort(),
      ).toEqual(['doc:doc-1', 'doc:doc-2']);
      expect(onDocumentEmpty.mock.calls.flat().sort()).toEqual(['doc-1', 'doc-2']);
    });

    it('defaults to a 60s sweep with a 300s idle allowance', () => {
      addClient('doc-1', 's1', 'u1');
      timers.push(presence.startCleanupInterval(io.asServer()));
      jest.setSystemTime(EPOCH + MAX_IDLE_MS + 1 - INTERVAL_MS);

      jest.advanceTimersByTime(INTERVAL_MS - 1);
      expect(awareness.getDocumentUserCount('doc-1')).toBe(1);

      jest.advanceTimersByTime(1);
      expect(awareness.getDocumentUserCount('doc-1')).toBe(0);
    });

    it('stops sweeping once the returned handle is cleared', () => {
      addClient('doc-1', 's1', 'u1');
      const handle = presence.startCleanupInterval(
        io.asServer(),
        INTERVAL_MS,
        MAX_IDLE_MS,
      );
      clearInterval(handle);
      jest.setSystemTime(EPOCH + MAX_IDLE_MS + 1);

      jest.advanceTimersByTime(INTERVAL_MS * 5);

      expect(awareness.getDocumentUserCount('doc-1')).toBe(1);
    });
  });
});
