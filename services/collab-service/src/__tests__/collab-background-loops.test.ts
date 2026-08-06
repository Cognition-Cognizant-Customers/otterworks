import { Server as SocketIOServer, Socket } from 'socket.io';
import * as Y from 'yjs';
import {
  CollaborationManager,
  setupCollaborationHandlers,
} from '../handlers/collaboration';
import { DocumentStore } from '../services/document-store';
import { AwarenessService } from '../services/awareness';
import { PresenceHandler } from '../handlers/presence';
import { MetricsCollector } from '../metrics';
import { createMockLogger, createMockRedis, MockRedis } from './helpers/collab-harness';

/**
 * WP-11 — the periodic persistence/snapshot loops, comment relays and the
 * anonymous-socket fallback.
 *
 * These paths are unreachable from a network test without waiting on real
 * time, so the Socket.IO server and its sockets are replaced by fakes and the
 * interval boundaries are stepped with Jest fake timers. Nothing here sleeps.
 */

const PERSIST_INTERVAL_MS = 30_000;
const SNAPSHOT_INTERVAL_MS = 300_000;
const FIXED_NOW = 1_700_000_000_000;

interface RoomEmit {
  room: string;
  event: string;
  payload: unknown;
}

/** Minimal stand-in for a connected Socket.IO socket. */
class FakeSocket {
  readonly handlers = new Map<string, (...args: unknown[]) => unknown>();
  readonly emitted: Array<{ event: string; payload: unknown }> = [];
  readonly roomEmits: RoomEmit[] = [];
  readonly joined: string[] = [];
  readonly left: string[] = [];

  constructor(
    readonly id: string,
    readonly user?: {
      userId: string;
      email: string;
      displayName: string;
      roles: string[];
    },
  ) {}

  on(event: string, handler: (...args: unknown[]) => unknown): this {
    this.handlers.set(event, handler);
    return this;
  }

  emit(event: string, payload?: unknown): boolean {
    this.emitted.push({ event, payload });
    return true;
  }

  join(room: string): void {
    this.joined.push(room);
  }

  leave(room: string): void {
    this.left.push(room);
  }

  to(room: string) {
    return {
      emit: (event: string, payload: unknown) => {
        this.roomEmits.push({ room, event, payload });
      },
    };
  }

  /** Delivers a client message to the handler the manager registered. */
  trigger(event: string, ...args: unknown[]): unknown {
    const handler = this.handlers.get(event);
    if (!handler) throw new Error(`no handler registered for ${event}`);
    return handler(...args);
  }

  asSocket(): Socket {
    return this as unknown as Socket;
  }
}

class FakeIo {
  readonly roomEmits: RoomEmit[] = [];
  private connectionHandler?: (socket: Socket) => void;

  on(event: string, handler: (socket: Socket) => void): this {
    if (event === 'connection') this.connectionHandler = handler;
    return this;
  }

  to(room: string) {
    return {
      emit: (event: string, payload: unknown) => {
        this.roomEmits.push({ room, event, payload });
      },
    };
  }

  connect(socket: FakeSocket): void {
    if (!this.connectionHandler) throw new Error('manager never started');
    this.connectionHandler(socket.asSocket());
  }

  asServer(): SocketIOServer {
    return this as unknown as SocketIOServer;
  }
}

describe('Collaboration background loops and comment relays (WP-11)', () => {
  let redis: MockRedis;
  let logger: ReturnType<typeof createMockLogger>;
  let io: FakeIo;
  let awareness: AwarenessService;
  let manager: CollaborationManager;

  beforeEach(() => {
    jest.useFakeTimers({ now: FIXED_NOW });
    redis = createMockRedis();
    logger = createMockLogger();
    io = new FakeIo();
    awareness = new AwarenessService(logger);
    manager = new CollaborationManager({
      io: io.asServer(),
      documentStore: new DocumentStore(redis, logger),
      awareness,
      presenceHandler: new PresenceHandler(awareness, logger),
      metrics: new MetricsCollector(),
      logger,
      persistIntervalMs: PERSIST_INTERVAL_MS,
      snapshotIntervalMs: SNAPSHOT_INTERVAL_MS,
    });
    manager.start();
  });

  afterEach(async () => {
    await manager.stop();
    jest.useRealTimers();
  });

  function user(id: string) {
    return {
      userId: id,
      email: `${id}@test.com`,
      displayName: id,
      roles: ['user'],
    };
  }

  async function connectAndJoin(
    socketId: string,
    documentId: string,
    identity = user(socketId),
  ): Promise<FakeSocket> {
    const socket = new FakeSocket(socketId, identity);
    io.connect(socket);
    await socket.trigger('join-document', { documentId });
    return socket;
  }

  function stateUpdate(text: string): string {
    const doc = new Y.Doc();
    doc.getText('content').insert(0, text);
    return Buffer.from(Y.encodeStateAsUpdate(doc)).toString('base64');
  }

  describe('periodic persistence loop', () => {
    it('does not persist before the first interval elapses (limit-1)', async () => {
      await connectAndJoin('sock-loop-1', 'doc-loop-1');
      redis.set.mockClear();

      await jest.advanceTimersByTimeAsync(PERSIST_INTERVAL_MS - 1);

      expect(redis.set).not.toHaveBeenCalled();
    });

    it('persists exactly once at the interval (limit)', async () => {
      await connectAndJoin('sock-loop-2', 'doc-loop-2');
      redis.set.mockClear();

      await jest.advanceTimersByTimeAsync(PERSIST_INTERVAL_MS);

      expect(redis.set).toHaveBeenCalledTimes(1);
      expect(redis.set).toHaveBeenCalledWith(
        'doc:state:doc-loop-2',
        expect.any(Buffer),
        86400,
      );
    });

    it('still has persisted only once one tick past the interval (limit+1)', async () => {
      await connectAndJoin('sock-loop-3', 'doc-loop-3');
      redis.set.mockClear();

      await jest.advanceTimersByTimeAsync(PERSIST_INTERVAL_MS + 1);

      expect(redis.set).toHaveBeenCalledTimes(1);
    });

    it('persists every open document on each pass', async () => {
      await connectAndJoin('sock-loop-4a', 'doc-loop-4a');
      await connectAndJoin('sock-loop-4b', 'doc-loop-4b');
      redis.set.mockClear();

      await jest.advanceTimersByTimeAsync(PERSIST_INTERVAL_MS * 2);

      expect(redis.set.mock.calls.map((call) => call[0]).sort()).toEqual([
        'doc:state:doc-loop-4a',
        'doc:state:doc-loop-4a',
        'doc:state:doc-loop-4b',
        'doc:state:doc-loop-4b',
      ]);
    });

    it('writes the current CRDT state, not the state at join time', async () => {
      const socket = await connectAndJoin('sock-loop-5', 'doc-loop-5');
      socket.trigger('document-update', {
        documentId: 'doc-loop-5',
        update: stateUpdate('written later'),
      });
      redis.set.mockClear();

      await jest.advanceTimersByTimeAsync(PERSIST_INTERVAL_MS);

      const restored = new Y.Doc();
      Y.applyUpdate(restored, new Uint8Array(redis.set.mock.calls[0][1] as Buffer));
      expect(restored.getText('content').toString()).toBe('written later');
    });

    it('keeps looping after a failed pass', async () => {
      await connectAndJoin('sock-loop-6', 'doc-loop-6');
      redis.set.mockClear();
      redis.set.mockRejectedValueOnce(new Error('redis down'));

      await jest.advanceTimersByTimeAsync(PERSIST_INTERVAL_MS);
      await jest.advanceTimersByTimeAsync(PERSIST_INTERVAL_MS);

      expect(redis.set).toHaveBeenCalledTimes(2);
      expect(logger.error).toHaveBeenCalledWith(
        expect.objectContaining({ documentId: 'doc-loop-6' }),
        'periodic_persistence_failed',
      );
    });

    it('stops persisting once the manager is stopped', async () => {
      await connectAndJoin('sock-loop-7', 'doc-loop-7');
      await manager.stop();
      redis.set.mockClear();

      await jest.advanceTimersByTimeAsync(PERSIST_INTERVAL_MS * 3);

      expect(redis.set).not.toHaveBeenCalled();
    });
  });

  describe('periodic snapshot loop', () => {
    it('takes no snapshot before the snapshot interval elapses (limit-1)', async () => {
      await connectAndJoin('sock-snap-1', 'doc-snap-loop-1');
      redis.lpush.mockClear();

      await jest.advanceTimersByTimeAsync(SNAPSHOT_INTERVAL_MS - 1);

      expect(redis.lpush).not.toHaveBeenCalled();
    });

    it('takes an auto-snapshot at the snapshot interval (limit)', async () => {
      await connectAndJoin('sock-snap-2', 'doc-snap-loop-2');
      redis.lpush.mockClear();

      await jest.advanceTimersByTimeAsync(SNAPSHOT_INTERVAL_MS);

      expect(redis.lpush).toHaveBeenCalledTimes(1);
      const [key, raw] = redis.lpush.mock.calls[0] as [string, string];
      expect(key).toBe('doc:snapshots:doc-snap-loop-2');
      expect(JSON.parse(raw)).toMatchObject({
        documentId: 'doc-snap-loop-2',
        createdBy: 'system',
        label: 'auto-snapshot',
        createdAt: new Date(FIXED_NOW + SNAPSHOT_INTERVAL_MS).toISOString(),
      });
    });

    it('has still taken a single snapshot one tick later (limit+1)', async () => {
      await connectAndJoin('sock-snap-3', 'doc-snap-loop-3');
      redis.lpush.mockClear();

      await jest.advanceTimersByTimeAsync(SNAPSHOT_INTERVAL_MS + 1);

      expect(redis.lpush).toHaveBeenCalledTimes(1);
    });

    it('logs and continues when a periodic snapshot fails', async () => {
      await connectAndJoin('sock-snap-4', 'doc-snap-loop-4');
      redis.lpush.mockRejectedValueOnce(new Error('redis down'));

      await jest.advanceTimersByTimeAsync(SNAPSHOT_INTERVAL_MS);

      expect(logger.error).toHaveBeenCalledWith(
        expect.objectContaining({ documentId: 'doc-snap-loop-4' }),
        'periodic_snapshot_failed',
      );
    });

    it('stops snapshotting once the manager is stopped', async () => {
      await connectAndJoin('sock-snap-5', 'doc-snap-loop-5');
      await manager.stop();
      redis.lpush.mockClear();

      await jest.advanceTimersByTimeAsync(SNAPSHOT_INTERVAL_MS * 2);

      expect(redis.lpush).not.toHaveBeenCalled();
    });
  });

  describe('failure paths', () => {
    it('acknowledges a join failure when the state store is unavailable', async () => {
      redis.get.mockRejectedValueOnce(new Error('redis down'));
      const socket = new FakeSocket('sock-fail-1', user('user-fail-1'));
      io.connect(socket);

      const ack = jest.fn();
      await socket.trigger('join-document', { documentId: 'doc-join-fails' }, ack);

      expect(ack).toHaveBeenCalledWith({
        success: false,
        error: 'Failed to join document',
      });
      expect(socket.left).toContain('doc:doc-join-fails');
      expect(awareness.getDocumentUserCount('doc-join-fails')).toBe(0);
    });

    it('logs but does not throw when the shutdown flush fails', async () => {
      await connectAndJoin('sock-fail-2', 'doc-shutdown-fails');
      redis.set.mockRejectedValueOnce(new Error('redis down'));

      await expect(manager.stop()).resolves.toBeUndefined();
      expect(logger.error).toHaveBeenCalledWith(
        expect.objectContaining({ documentId: 'doc-shutdown-fails' }),
        'document_persist_on_shutdown_failed',
      );
    });

    it('is a no-op when cleanup runs for a document that is already evicted', async () => {
      const socket = await connectAndJoin('sock-fail-3', 'doc-already-gone');
      socket.trigger('leave-document', { documentId: 'doc-already-gone' });
      await jest.advanceTimersByTimeAsync(0);
      expect(manager.getDocument('doc-already-gone')).toBeUndefined();
      redis.set.mockClear();

      await expect(
        manager.persistAndCleanupDocument('doc-already-gone'),
      ).resolves.toBeUndefined();
      expect(redis.set).not.toHaveBeenCalled();
    });
  });

  describe('comment relays', () => {
    it('relays a comment update to the room but not back to the sender', async () => {
      const socket = await connectAndJoin('sock-comment-1', 'doc-comments');

      socket.trigger('comment-update', {
        documentId: 'doc-comments',
        commentId: 'c-1',
        content: 'edited',
      });

      expect(socket.roomEmits).toContainEqual({
        room: 'doc:doc-comments',
        event: 'comment-updated',
        payload: expect.objectContaining({
          commentId: 'c-1',
          content: 'edited',
          updatedBy: { userId: 'sock-comment-1', displayName: 'sock-comment-1' },
          updatedAt: new Date(FIXED_NOW).toISOString(),
        }),
      });
      expect(socket.emitted.map((entry) => entry.event)).not.toContain('comment-updated');
    });

    it('relays a comment deletion with the deleting user from the token', async () => {
      const socket = await connectAndJoin('sock-comment-2', 'doc-comments-del');

      socket.trigger('comment-delete', {
        documentId: 'doc-comments-del',
        commentId: 'c-2',
      });

      expect(socket.roomEmits).toContainEqual({
        room: 'doc:doc-comments-del',
        event: 'comment-deleted',
        payload: { commentId: 'c-2', deletedBy: 'sock-comment-2' },
      });
    });

    it('stamps an added comment with a server timestamp and the authenticated author', async () => {
      const socket = await connectAndJoin('sock-comment-3', 'doc-comments-add');

      socket.trigger('comment-add', {
        documentId: 'doc-comments-add',
        comment: {
          id: 'c-3',
          threadId: 't-3',
          content: 'hello',
          rangeStart: 0,
          rangeEnd: 5,
        },
      });

      const added = socket.roomEmits.find((entry) => entry.event === 'comment-added');
      expect(added).toMatchObject({
        room: 'doc:doc-comments-add',
        event: 'comment-added',
        payload: {
          id: 'c-3',
          author: { userId: 'sock-comment-3', displayName: 'sock-comment-3' },
          createdAt: new Date(FIXED_NOW).toISOString(),
        },
      });
    });
  });

  describe('unauthenticated socket fallback', () => {
    it('falls back to an anonymous identity when the socket carries no user', async () => {
      const socket = new FakeSocket('sock-anon');
      io.connect(socket);

      await socket.trigger('join-document', { documentId: 'doc-anon' });

      expect(awareness.getDocumentUsers('doc-anon')).toEqual([
        expect.objectContaining({
          userId: 'anon-sock-anon',
          displayName: 'Anonymous',
        }),
      ]);
    });
  });

  describe('setupCollaborationHandlers', () => {
    it('returns a started manager wired to the given server', async () => {
      const legacyIo = new FakeIo();
      const legacyAwareness = new AwarenessService(logger);
      const legacyManager = setupCollaborationHandlers(
        legacyIo.asServer(),
        new DocumentStore(redis, logger),
        legacyAwareness,
        new PresenceHandler(legacyAwareness, logger),
        new MetricsCollector(),
        logger,
        PERSIST_INTERVAL_MS,
        SNAPSHOT_INTERVAL_MS,
      );

      const socket = new FakeSocket('sock-legacy', user('legacy-user'));
      legacyIo.connect(socket);
      await socket.trigger('join-document', { documentId: 'doc-legacy' });
      redis.set.mockClear();
      await jest.advanceTimersByTimeAsync(PERSIST_INTERVAL_MS);

      expect(legacyManager.getDocument('doc-legacy')).toBeDefined();
      expect(redis.set).toHaveBeenCalledWith(
        'doc:state:doc-legacy',
        expect.any(Buffer),
        86400,
      );
      await legacyManager.stop();
    });

    it('defaults its intervals when none are supplied', async () => {
      const legacyIo = new FakeIo();
      const legacyAwareness = new AwarenessService(logger);
      const legacyManager = setupCollaborationHandlers(
        legacyIo.asServer(),
        new DocumentStore(redis, logger),
        legacyAwareness,
        new PresenceHandler(legacyAwareness, logger),
        new MetricsCollector(),
        logger,
      );

      const socket = new FakeSocket('sock-legacy-2', user('legacy-user-2'));
      legacyIo.connect(socket);
      await socket.trigger('join-document', { documentId: 'doc-legacy-defaults' });
      redis.set.mockClear();
      await jest.advanceTimersByTimeAsync(29_999);
      expect(redis.set).not.toHaveBeenCalled();

      await jest.advanceTimersByTimeAsync(1);
      expect(redis.set).toHaveBeenCalledTimes(1);
      await legacyManager.stop();
    });
  });
});
