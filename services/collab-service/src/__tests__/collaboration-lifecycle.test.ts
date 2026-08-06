import { setupCollaborationHandlers } from '../handlers/collaboration';
import { PresenceHandler } from '../handlers/presence';
import { AwarenessService } from '../services/awareness';
import { DocumentStore } from '../services/document-store';
import { MetricsCollector } from '../metrics';
import { createHarness, flushAsync, type Harness } from './helpers/collab-harness';
import { FakeIO, FakeSocket } from './helpers/fake-socket';
import { InMemoryRedis } from './helpers/in-memory-redis';
import { createTestLogger } from './helpers/test-logger';
import { createReplica, readDoc } from './helpers/yjs';

/**
 * Disconnect, room teardown and the background persistence/snapshot loops.
 *
 * Time is controlled with jest fake timers so the interval boundaries can be asserted
 * exactly; nothing here waits on wall-clock time.
 */
describe('CollaborationManager lifecycle', () => {
  const DOC = 'doc-lifecycle';
  const ROOM = `doc:${DOC}`;

  function validUpdate(text: string): string {
    const replica = createReplica({ clientId: 42 });
    replica.text().insert(0, text);
    return replica.lastUpdate();
  }

  function saveCount(h: Harness): number {
    return h.redis.calls.filter((c) => c === 'set').length;
  }

  describe('disconnect handling', () => {
    let h: Harness;

    beforeEach(() => {
      h = createHarness();
    });

    afterEach(async () => {
      await h.stop();
    });

    it.each([
      ['a clean namespace disconnect', 'client namespace disconnect'],
      ['an abrupt transport error', 'transport error'],
      ['a ping timeout', 'ping timeout'],
      ['a server-initiated disconnect', 'server namespace disconnect'],
    ])('cleans up presence after %s', async (_label, reason) => {
      const { socket: alice } = await h.join('sock-a', 'user-a', DOC);
      await h.join('sock-b', 'user-b', DOC);
      alice.clearRecorded();
      h.io.clearRecorded();

      h.io.disconnect(alice, reason);

      expect(h.awareness.getUserDocument(alice.id)).toBeNull();
      expect(h.awareness.getDocumentUserCount(DOC)).toBe(1);
      expect(alice.broadcastsOf('user-left')).toEqual([
        {
          room: ROOM,
          event: 'user-left',
          payload: { socketId: alice.id, userId: 'user-a' },
        },
      ]);
      expect(h.io.emitsOf('presence-update')).toHaveLength(1);
    });

    it('reports the same presence count for a clean and an abrupt disconnect', async () => {
      const clean = createHarness();
      const abrupt = createHarness();
      try {
        for (const [harness, reason] of [
          [clean, 'client namespace disconnect'],
          [abrupt, 'transport error'],
        ] as const) {
          const { socket } = await harness.join('sock-a', 'user-a', DOC);
          await harness.join('sock-b', 'user-b', DOC);
          harness.io.disconnect(socket, reason);
        }
        const summarise = (harness: Harness) => {
          const presence = harness.presenceHandler.getDocumentPresence(DOC);
          return {
            count: presence.count,
            users: presence.users.map((u) => ({ userId: u.userId, color: u.color })),
          };
        };
        expect(summarise(abrupt)).toEqual(summarise(clean));
      } finally {
        await clean.stop();
        await abrupt.stop();
      }
    });

    it('ignores the disconnect of a socket that never joined a document', async () => {
      const socket = h.connect('sock-idle', 'user-idle');

      h.io.disconnect(socket, 'transport error');

      expect(socket.broadcastsOf('user-left')).toHaveLength(0);
      expect(h.io.emitsOf('presence-update')).toHaveLength(0);
      expect(await h.gaugeValue('activeConnections')).toBe(0);
    });

    it('treats a repeated disconnect for the same socket as a no-op', async () => {
      const { socket } = await h.join('sock-a', 'user-a', DOC);
      await h.join('sock-b', 'user-b', DOC);
      socket.clearRecorded();

      socket.fire('disconnect', 'transport error');
      socket.fire('disconnect', 'transport error');

      expect(socket.broadcastsOf('user-left')).toHaveLength(1);
      expect(h.awareness.getDocumentUserCount(DOC)).toBe(1);
    });

    it.each([
      ['no clients', 0],
      ['one client', 1],
      ['two clients', 2],
    ])('tracks activeConnections with %s connected', async (_label, count) => {
      for (let i = 0; i < count; i++) {
        await h.join(`sock-${i}`, `user-${i}`, DOC);
      }

      expect(await h.gaugeValue('activeConnections')).toBe(count);
    });

    it('returns activeConnections to zero once every client has gone', async () => {
      const { socket: a } = await h.join('sock-a', 'user-a', DOC);
      const { socket: b } = await h.join('sock-b', 'user-b', DOC);

      h.io.disconnect(a, 'transport close');
      h.io.disconnect(b, 'transport close');

      expect(await h.gaugeValue('activeConnections')).toBe(0);
    });
  });

  describe('room teardown and re-open', () => {
    let h: Harness;

    beforeEach(() => {
      h = createHarness();
    });

    afterEach(async () => {
      await h.stop();
    });

    it('persists and evicts the document when the last client disconnects', async () => {
      const { socket } = await h.join('sock-a', 'user-a', DOC);
      await socket.fire('document-update', {
        documentId: DOC,
        update: validUpdate('final text'),
      });

      h.io.disconnect(socket, 'transport close');
      await flushAsync();

      expect(h.manager.getDocument(DOC)).toBeUndefined();
      expect(await h.gaugeValue('activeRooms')).toBe(0);
      expect(h.redis.strings.has(`doc:state:${DOC}`)).toBe(true);
      expect(h.logger.messages('info')).toContain('document_persisted_on_cleanup');
    });

    it('keeps the document in memory while another client remains', async () => {
      const { socket: a } = await h.join('sock-a', 'user-a', DOC);
      await h.join('sock-b', 'user-b', DOC);

      h.io.disconnect(a, 'transport close');
      await flushAsync();

      expect(h.manager.getDocument(DOC)).toBeDefined();
      expect(await h.gaugeValue('activeRooms')).toBe(1);
    });

    it('restores the persisted text when a new client re-opens the room', async () => {
      const { socket: first } = await h.join('sock-a', 'user-a', DOC);
      await first.fire('document-update', {
        documentId: DOC,
        update: validUpdate('survives teardown'),
      });
      h.io.disconnect(first, 'transport close');
      await flushAsync();
      expect(h.manager.getDocumentCount()).toBe(0);

      const { socket: second } = await h.join('sock-b', 'user-b', DOC);

      expect(readDoc(h.manager.getDocument(DOC)!)).toBe('survives teardown');
      expect(createReplica({ state: h.lastSyncState(second) }).read()).toBe(
        'survives teardown',
      );
    });

    it('keeps the document in memory when the cleanup persist fails', async () => {
      const { socket } = await h.join('sock-a', 'user-a', DOC);
      await socket.fire('document-update', {
        documentId: DOC,
        update: validUpdate('unsaved'),
      });
      h.redis.failOn('set');

      h.io.disconnect(socket, 'transport close');
      await flushAsync();

      expect(readDoc(h.manager.getDocument(DOC)!)).toBe('unsaved');
      expect(h.logger.messages('error')).toContain('document_persist_on_cleanup_failed');
    });

    it('keeps the document when a client re-joins while cleanup is still persisting', async () => {
      const { socket } = await h.join('sock-a', 'user-a', DOC);
      await socket.fire('document-update', {
        documentId: DOC,
        update: validUpdate('rejoined mid-cleanup'),
      });

      let releaseSave = (): void => undefined;
      h.redis.gateOn('set', new Promise<void>((resolve) => (releaseSave = resolve)));
      h.io.disconnect(socket, 'transport close');

      // A new client arrives before the persistence write completes.
      const rejoiner = h.connect('sock-b', 'user-b');
      const joinPromise = h.joinWith(rejoiner, DOC);
      releaseSave();
      h.redis.clearGate('set');
      await joinPromise;
      await flushAsync();

      expect(readDoc(h.manager.getDocument(DOC)!)).toBe('rejoined mid-cleanup');
      expect(h.awareness.getDocumentUserCount(DOC)).toBe(1);
    });

    it('persists once when cleanup is requested concurrently for the same document', async () => {
      const { socket } = await h.join('sock-a', 'user-a', DOC);
      await socket.fire('document-update', {
        documentId: DOC,
        update: validUpdate('once'),
      });
      const before = saveCount(h);

      await Promise.all([
        h.manager.persistAndCleanupDocument(DOC),
        h.manager.persistAndCleanupDocument(DOC),
        h.manager.persistAndCleanupDocument(DOC),
      ]);

      expect(saveCount(h) - before).toBe(1);
    });

    it('does nothing when cleaning up a document that is not in memory', async () => {
      const before = saveCount(h);

      await h.manager.persistAndCleanupDocument('never-opened');

      expect(saveCount(h)).toBe(before);
    });
  });

  describe('leaving a document explicitly', () => {
    let h: Harness;

    beforeEach(() => {
      h = createHarness();
    });

    afterEach(async () => {
      await h.stop();
    });

    it('notifies the room and clears presence', async () => {
      const { socket } = await h.join('sock-a', 'user-a', DOC);
      await h.join('sock-b', 'user-b', DOC);
      socket.clearRecorded();
      h.io.clearRecorded();

      socket.fire('leave-document', { documentId: DOC });

      expect(socket.rooms.has(ROOM)).toBe(false);
      expect(socket.broadcastsOf('user-left')).toHaveLength(1);
      expect(h.io.emitsOf('presence-update')).toHaveLength(1);
      expect(h.awareness.getDocumentUserCount(DOC)).toBe(1);
    });

    it('does not announce a leave for a socket that never joined', async () => {
      const socket = h.connect('sock-ghost', 'user-ghost');

      socket.fire('leave-document', { documentId: DOC });

      expect(socket.broadcastsOf('user-left')).toHaveLength(0);
      expect(h.io.emitsOf('presence-update')).toHaveLength(0);
    });

    it('leaves the tracked document, not the one named in the payload', async () => {
      const { socket } = await h.join('sock-a', 'user-a', DOC);
      await h.join('sock-b', 'user-b', 'doc-other');
      socket.clearRecorded();

      socket.fire('leave-document', { documentId: 'doc-other' });

      expect(socket.broadcastsOf('user-left')[0].room).toBe(ROOM);
      expect(h.awareness.getDocumentUserCount('doc-other')).toBe(1);
    });

    it('tears the room down when the last member leaves and re-opens it on re-join', async () => {
      const { socket } = await h.join('sock-a', 'user-a', DOC);
      await socket.fire('document-update', {
        documentId: DOC,
        update: validUpdate('left behind'),
      });

      socket.fire('leave-document', { documentId: DOC });
      await flushAsync();
      expect(h.manager.getDocumentCount()).toBe(0);

      await h.joinWith(socket, DOC);

      expect(readDoc(h.manager.getDocument(DOC)!)).toBe('left behind');
    });
  });

  describe('setupCollaborationHandlers compatibility wrapper', () => {
    it('returns a started manager wired to the same collaborators', async () => {
      const logger = createTestLogger();
      const redis = new InMemoryRedis();
      const awareness = new AwarenessService(logger.asLogger());
      const io = new FakeIO();
      const manager = setupCollaborationHandlers(
        io.asServer(),
        new DocumentStore(redis.asAdapter(), logger.asLogger()),
        awareness,
        new PresenceHandler(awareness, logger.asLogger()),
        new MetricsCollector(),
        logger.asLogger(),
      );

      try {
        const socket = io.connect(
          new FakeSocket('sock-compat', {
            userId: 'user-compat',
            email: 'compat@test',
            displayName: 'Compat',
            roles: [],
          }),
        );
        await socket.fire('join-document', { documentId: DOC });

        expect(manager.getDocumentCount()).toBe(1);
        expect(awareness.getDocumentUserCount(DOC)).toBe(1);
      } finally {
        await manager.stop();
      }
    });
  });

  describe('background persistence and snapshot loops', () => {
    const PERSIST_MS = 1_000;
    const SNAPSHOT_MS = 5_000;
    let h: Harness;

    beforeEach(async () => {
      jest.useFakeTimers({ doNotFake: ['setImmediate'] });
      h = createHarness({
        persistIntervalMs: PERSIST_MS,
        snapshotIntervalMs: SNAPSHOT_MS,
      });
      const { socket } = await h.join('sock-a', 'user-a', DOC);
      await socket.fire('document-update', {
        documentId: DOC,
        update: validUpdate('loop content'),
      });
    });

    afterEach(async () => {
      await h.stop();
      jest.useRealTimers();
    });

    it.each([
      ['just before the interval', PERSIST_MS - 1, 0],
      ['exactly at the interval', PERSIST_MS, 1],
      ['just after the interval', PERSIST_MS + 1, 1],
      ['after two intervals', PERSIST_MS * 2, 2],
    ])(
      'runs the persistence loop %s (%ims) %i time(s)',
      async (_label, elapsedMs, expectedRuns) => {
        const before = saveCount(h);

        await jest.advanceTimersByTimeAsync(elapsedMs);

        expect(saveCount(h) - before).toBe(expectedRuns);
      },
    );

    it.each([
      ['just before the interval', SNAPSHOT_MS - 1, 0],
      ['exactly at the interval', SNAPSHOT_MS, 1],
      ['just after the interval', SNAPSHOT_MS + 1, 1],
    ])(
      'runs the snapshot loop %s (%ims) %i time(s)',
      async (_label, elapsedMs, expectedRuns) => {
        await jest.advanceTimersByTimeAsync(elapsedMs);

        expect(h.redis.lists.get(`doc:snapshots:${DOC}`) ?? []).toHaveLength(
          expectedRuns,
        );
      },
    );

    it('labels loop-generated snapshots as system auto-snapshots', async () => {
      await jest.advanceTimersByTimeAsync(SNAPSHOT_MS);

      const [raw] = h.redis.lists.get(`doc:snapshots:${DOC}`) ?? [];
      expect(JSON.parse(raw)).toMatchObject({
        createdBy: 'system',
        label: 'auto-snapshot',
      });
    });

    it('keeps persisting on later ticks after a failed write', async () => {
      h.redis.failOn('set');
      await jest.advanceTimersByTimeAsync(PERSIST_MS);
      expect(h.logger.messages('error')).toContain('periodic_persistence_failed');

      h.redis.clearFailure('set');
      await jest.advanceTimersByTimeAsync(PERSIST_MS);

      expect(h.redis.strings.has(`doc:state:${DOC}`)).toBe(true);
    });

    it('keeps snapshotting on later ticks after a failed snapshot', async () => {
      h.redis.failOn('lpush');
      await jest.advanceTimersByTimeAsync(SNAPSHOT_MS);
      expect(h.logger.messages('error')).toContain('periodic_snapshot_failed');

      h.redis.clearFailure('lpush');
      await jest.advanceTimersByTimeAsync(SNAPSHOT_MS);

      expect(h.redis.lists.get(`doc:snapshots:${DOC}`) ?? []).toHaveLength(1);
    });

    it('flushes every in-memory document on stop', async () => {
      await h.joinWith(h.connect('sock-b', 'user-b'), 'doc-second');
      const before = saveCount(h);

      await h.manager.stop();

      expect(saveCount(h) - before).toBe(2);
      expect(h.logger.messages('info')).toContain('document_persisted_on_shutdown');
    });

    it('stops the loops so no further writes happen after shutdown', async () => {
      await h.manager.stop();
      const after = saveCount(h);

      await jest.advanceTimersByTimeAsync(PERSIST_MS * 5 + SNAPSHOT_MS);

      expect(saveCount(h)).toBe(after);
    });

    it('does not throw when the shutdown flush fails', async () => {
      h.redis.failOn('set');

      await expect(h.manager.stop()).resolves.toBeUndefined();

      expect(h.logger.messages('error')).toContain('document_persist_on_shutdown_failed');
    });
  });
});
