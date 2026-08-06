import type { Server as SocketIOServer } from 'socket.io';
import { AwarenessService } from '../services/awareness';
import { PresenceHandler } from '../handlers/presence';

/**
 * WP-11 — presence snapshotting and the stale-session reaper.
 *
 * Determinism: the reaper is interval driven, so the whole file runs on fake
 * timers; no test waits on wall time.
 */

const FIXED_NOW = 1_700_000_000_000;
const INTERVAL_MS = 60_000;
const MAX_IDLE_MS = 300_000;

function makeLogger() {
  return {
    info: jest.fn(),
    warn: jest.fn(),
    error: jest.fn(),
    debug: jest.fn(),
    fatal: jest.fn(),
    trace: jest.fn(),
    child: jest.fn().mockReturnThis(),
    level: 'info',
  } as never;
}

interface FakeSocket {
  id: string;
  emit: jest.Mock;
  leave: jest.Mock;
  to: jest.Mock;
  roomEmit: jest.Mock;
}

function makeSocket(id: string): FakeSocket {
  const roomEmit = jest.fn();
  return {
    id,
    emit: jest.fn(),
    leave: jest.fn(),
    to: jest.fn(() => ({ emit: roomEmit })),
    roomEmit,
  };
}

interface FakeIo {
  io: SocketIOServer;
  roomEmit: jest.Mock;
  to: jest.Mock;
  sockets: Map<string, FakeSocket>;
}

function makeIo(): FakeIo {
  const roomEmit = jest.fn();
  const sockets = new Map<string, FakeSocket>();
  const to = jest.fn(() => ({ emit: roomEmit }));
  const io = {
    to,
    sockets: { sockets },
  } as unknown as SocketIOServer;
  return { io, roomEmit, to, sockets };
}

describe('PresenceHandler (WP-11)', () => {
  let awareness: AwarenessService;
  let presence: PresenceHandler;
  let fakeIo: FakeIo;
  let timer: NodeJS.Timeout | null;

  beforeEach(() => {
    jest.useFakeTimers({ now: FIXED_NOW });
    awareness = new AwarenessService(makeLogger());
    presence = new PresenceHandler(awareness, makeLogger());
    fakeIo = makeIo();
    timer = null;
  });

  afterEach(() => {
    if (timer) clearInterval(timer);
    jest.useRealTimers();
  });

  describe('getDocumentPresence', () => {
    it('returns the users and count for a populated document', () => {
      awareness.addUser('doc-p', 's1', 'u1', 'Alice', 'a@t.com');
      awareness.addUser('doc-p', 's2', 'u2', 'Bob', 'b@t.com');

      const info = presence.getDocumentPresence('doc-p');

      expect(info.documentId).toBe('doc-p');
      expect(info.count).toBe(2);
      expect(info.users.map((u) => u.userId).sort()).toEqual(['u1', 'u2']);
    });

    it('returns an empty presence for an unknown document', () => {
      expect(presence.getDocumentPresence('nope')).toEqual({
        documentId: 'nope',
        users: [],
        count: 0,
      });
    });
  });

  describe('getActiveDocuments', () => {
    it('lists every active document with its user count', () => {
      awareness.addUser('doc-1', 's1', 'u1', 'Alice', 'a@t.com');
      awareness.addUser('doc-1', 's2', 'u2', 'Bob', 'b@t.com');
      awareness.addUser('doc-2', 's3', 'u3', 'Carol', 'c@t.com');

      const docs = presence
        .getActiveDocuments()
        .sort((a, b) => a.documentId.localeCompare(b.documentId));

      expect(docs).toEqual([
        { documentId: 'doc-1', userCount: 2 },
        { documentId: 'doc-2', userCount: 1 },
      ]);
    });

    it('returns an empty list when nothing is active', () => {
      expect(presence.getActiveDocuments()).toEqual([]);
    });
  });

  describe('broadcastPresenceUpdate', () => {
    it('emits presence-update to the document room only', () => {
      awareness.addUser('doc-p', 's1', 'u1', 'Alice', 'a@t.com');

      presence.broadcastPresenceUpdate(fakeIo.io, 'doc-p');

      expect(fakeIo.to).toHaveBeenCalledWith('doc:doc-p');
      expect(fakeIo.roomEmit).toHaveBeenCalledWith(
        'presence-update',
        expect.objectContaining({ documentId: 'doc-p', count: 1 }),
      );
    });

    it('broadcasts an empty presence for a document with no users', () => {
      presence.broadcastPresenceUpdate(fakeIo.io, 'doc-empty');

      expect(fakeIo.roomEmit).toHaveBeenCalledWith('presence-update', {
        documentId: 'doc-empty',
        users: [],
        count: 0,
      });
    });
  });

  describe('startCleanupInterval', () => {
    it('defaults to a 60s sweep with a 300s idle limit when no timings are given', () => {
      awareness.addUser('doc-p', 's1', 'u1', 'Alice', 'a@t.com');
      const socket = makeSocket('s1');
      fakeIo.sockets.set('s1', socket);

      timer = presence.startCleanupInterval(fakeIo.io);

      jest.advanceTimersByTime(300_000);
      expect(awareness.getDocumentUserCount('doc-p')).toBe(1);

      jest.advanceTimersByTime(60_000);
      expect(awareness.getDocumentUserCount('doc-p')).toBe(0);
      expect(socket.emit).toHaveBeenCalledWith('session-expired', {
        documentId: 'doc-p',
        reason: 'idle_timeout',
      });
    });

    it('does nothing before the first interval elapses (limit-1)', () => {
      awareness.addUser('doc-p', 's1', 'u1', 'Alice', 'a@t.com');
      const socket = makeSocket('s1');
      fakeIo.sockets.set('s1', socket);

      timer = presence.startCleanupInterval(
        fakeIo.io,
        INTERVAL_MS,
        MAX_IDLE_MS,
        jest.fn(),
      );
      jest.advanceTimersByTime(INTERVAL_MS - 1);

      expect(socket.emit).not.toHaveBeenCalled();
      expect(awareness.getDocumentUserCount('doc-p')).toBe(1);
    });

    it('evicts an idle socket on the first tick past the idle limit and tells it why', () => {
      awareness.addUser('doc-p', 's1', 'u1', 'Alice', 'a@t.com');
      const socket = makeSocket('s1');
      fakeIo.sockets.set('s1', socket);

      timer = presence.startCleanupInterval(
        fakeIo.io,
        INTERVAL_MS,
        MAX_IDLE_MS,
        jest.fn(),
      );
      jest.advanceTimersByTime(MAX_IDLE_MS + INTERVAL_MS);

      expect(socket.emit).toHaveBeenCalledWith('session-expired', {
        documentId: 'doc-p',
        reason: 'idle_timeout',
      });
      expect(socket.leave).toHaveBeenCalledWith('doc:doc-p');
      expect(socket.roomEmit).toHaveBeenCalledWith('user-left', {
        socketId: 's1',
        userId: 'u1',
      });
      expect(awareness.getDocumentUserCount('doc-p')).toBe(0);
    });

    it('keeps a socket whose activity is refreshed before every tick', () => {
      awareness.addUser('doc-p', 's1', 'u1', 'Alice', 'a@t.com');
      const socket = makeSocket('s1');
      fakeIo.sockets.set('s1', socket);

      timer = presence.startCleanupInterval(
        fakeIo.io,
        INTERVAL_MS,
        MAX_IDLE_MS,
        jest.fn(),
      );

      for (let i = 0; i < 10; i++) {
        jest.advanceTimersByTime(INTERVAL_MS);
        awareness.refreshActivity('s1');
      }

      expect(socket.emit).not.toHaveBeenCalled();
      expect(awareness.getDocumentUserCount('doc-p')).toBe(1);
    });

    it('invokes onDocumentEmpty exactly once when the last user is reaped', () => {
      awareness.addUser('doc-p', 's1', 'u1', 'Alice', 'a@t.com');
      fakeIo.sockets.set('s1', makeSocket('s1'));
      const onEmpty = jest.fn();

      timer = presence.startCleanupInterval(fakeIo.io, INTERVAL_MS, MAX_IDLE_MS, onEmpty);
      jest.advanceTimersByTime(MAX_IDLE_MS + INTERVAL_MS * 3);

      expect(onEmpty).toHaveBeenCalledTimes(1);
      expect(onEmpty).toHaveBeenCalledWith('doc-p');
    });

    it('does not invoke onDocumentEmpty when another user survives the sweep', () => {
      awareness.addUser('doc-p', 'stale', 'u1', 'Alice', 'a@t.com');
      awareness.addUser('doc-p', 'active', 'u2', 'Bob', 'b@t.com');
      fakeIo.sockets.set('stale', makeSocket('stale'));
      fakeIo.sockets.set('active', makeSocket('active'));
      const onEmpty = jest.fn();

      timer = presence.startCleanupInterval(fakeIo.io, INTERVAL_MS, MAX_IDLE_MS, onEmpty);
      jest.advanceTimersByTime(MAX_IDLE_MS);
      awareness.refreshActivity('active');
      jest.advanceTimersByTime(INTERVAL_MS);

      expect(onEmpty).not.toHaveBeenCalled();
      expect(awareness.getDocumentUserCount('doc-p')).toBe(1);
    });

    it('survives a reaped socket that is no longer registered on the server', () => {
      awareness.addUser('doc-p', 'ghost', 'u1', 'Alice', 'a@t.com');
      const onEmpty = jest.fn();

      timer = presence.startCleanupInterval(fakeIo.io, INTERVAL_MS, MAX_IDLE_MS, onEmpty);

      expect(() => jest.advanceTimersByTime(MAX_IDLE_MS + INTERVAL_MS)).not.toThrow();
      expect(onEmpty).toHaveBeenCalledWith('doc-p');
      expect(fakeIo.roomEmit).toHaveBeenCalledWith(
        'presence-update',
        expect.objectContaining({ documentId: 'doc-p', count: 0 }),
      );
    });

    it('works without an onDocumentEmpty callback', () => {
      awareness.addUser('doc-p', 's1', 'u1', 'Alice', 'a@t.com');
      fakeIo.sockets.set('s1', makeSocket('s1'));

      timer = presence.startCleanupInterval(fakeIo.io, INTERVAL_MS, MAX_IDLE_MS);

      expect(() => jest.advanceTimersByTime(MAX_IDLE_MS + INTERVAL_MS)).not.toThrow();
      expect(awareness.getDocumentUserCount('doc-p')).toBe(0);
    });

    it('broadcasts presence for each affected document once per sweep', () => {
      awareness.addUser('doc-1', 's1', 'u1', 'Alice', 'a@t.com');
      awareness.addUser('doc-2', 's2', 'u2', 'Bob', 'b@t.com');
      fakeIo.sockets.set('s1', makeSocket('s1'));
      fakeIo.sockets.set('s2', makeSocket('s2'));

      timer = presence.startCleanupInterval(
        fakeIo.io,
        INTERVAL_MS,
        MAX_IDLE_MS,
        jest.fn(),
      );
      jest.advanceTimersByTime(MAX_IDLE_MS + INTERVAL_MS);

      const presenceRooms = fakeIo.to.mock.calls.map(([room]) => room).sort();
      expect(presenceRooms).toEqual(['doc:doc-1', 'doc:doc-2']);
    });

    it('stops sweeping once the interval is cleared', () => {
      awareness.addUser('doc-p', 's1', 'u1', 'Alice', 'a@t.com');
      fakeIo.sockets.set('s1', makeSocket('s1'));

      const localTimer = presence.startCleanupInterval(
        fakeIo.io,
        INTERVAL_MS,
        MAX_IDLE_MS,
        jest.fn(),
      );
      clearInterval(localTimer);
      jest.advanceTimersByTime(MAX_IDLE_MS * 10);

      expect(awareness.getDocumentUserCount('doc-p')).toBe(1);
    });
  });
});
