import { CollaborationManager } from '../../handlers/collaboration';
import { PresenceHandler } from '../../handlers/presence';
import { AwarenessService } from '../../services/awareness';
import { DocumentStore } from '../../services/document-store';
import { MetricsCollector } from '../../metrics';
import { FakeIO, FakeSocket } from './fake-socket';
import { InMemoryRedis } from './in-memory-redis';
import { createTestLogger, type TestLogger } from './test-logger';

/**
 * Drain the microtask queue and one macrotask turn so that fire-and-forget work started
 * by a handler (e.g. persistAndCleanupDocument) has settled. This is a queue flush, not
 * a timed wait — it does not depend on how long anything takes.
 */
export function flushAsync(): Promise<void> {
  return new Promise((resolve) => setImmediate(resolve));
}

export interface JoinAck {
  success: boolean;
  error?: string;
}

export interface HarnessOptions {
  persistIntervalMs?: number;
  snapshotIntervalMs?: number;
  maxSnapshots?: number;
  documentTtl?: number;
}

export interface Harness {
  io: FakeIO;
  redis: InMemoryRedis;
  documentStore: DocumentStore;
  awareness: AwarenessService;
  presenceHandler: PresenceHandler;
  metrics: MetricsCollector;
  manager: CollaborationManager;
  logger: TestLogger;
  connect(socketId: string, userId: string, displayName?: string): FakeSocket;
  /** Connect a socket and join a document, returning the socket and the ack. */
  join(
    socketId: string,
    userId: string,
    documentId: string,
  ): Promise<{ socket: FakeSocket; ack: JoinAck }>;
  joinWith(socket: FakeSocket, documentId: string): Promise<JoinAck>;
  /** The base64 state the server pushed to a socket in its last sync-document. */
  lastSyncState(socket: FakeSocket): string;
  gaugeValue(name: 'activeRooms' | 'activeConnections'): Promise<number>;
  counterValue(
    name: 'documentUpdatesTotal' | 'connectionErrors' | 'presenceUpdatesTotal',
    labels?: Record<string, string>,
  ): Promise<number>;
  stop(): Promise<void>;
}

export function createHarness(options: HarnessOptions = {}): Harness {
  const logger = createTestLogger();
  const io = new FakeIO();
  const redis = new InMemoryRedis();
  const documentStore = new DocumentStore(redis.asAdapter(), logger.asLogger(), {
    maxSnapshots: options.maxSnapshots,
    documentTtl: options.documentTtl,
  });
  const awareness = new AwarenessService(logger.asLogger());
  const presenceHandler = new PresenceHandler(awareness, logger.asLogger());
  const metrics = new MetricsCollector();

  const manager = new CollaborationManager({
    io: io.asServer(),
    documentStore,
    awareness,
    presenceHandler,
    metrics,
    logger: logger.asLogger(),
    persistIntervalMs: options.persistIntervalMs ?? 30_000,
    snapshotIntervalMs: options.snapshotIntervalMs ?? 300_000,
  });
  manager.start();

  function connect(socketId: string, userId: string, displayName?: string): FakeSocket {
    const socket = new FakeSocket(socketId, {
      userId,
      email: `${userId}@otterworks.test`,
      displayName: displayName ?? userId,
      roles: ['user'],
    });
    return io.connect(socket);
  }

  async function joinWith(socket: FakeSocket, documentId: string): Promise<JoinAck> {
    let ack: JoinAck = { success: false, error: 'ack never invoked' };
    await socket.fire('join-document', { documentId }, (res: JoinAck) => {
      ack = res;
    });
    return ack;
  }

  return {
    io,
    redis,
    documentStore,
    awareness,
    presenceHandler,
    metrics,
    manager,
    logger,
    connect,
    joinWith,
    async join(socketId, userId, documentId) {
      const socket = connect(socketId, userId);
      const ack = await joinWith(socket, documentId);
      return { socket, ack };
    },
    lastSyncState(socket) {
      const syncs = socket.emitsOf('sync-document') as Array<{ state: string }>;
      const last = syncs[syncs.length - 1];
      if (!last) throw new Error(`socket ${socket.id} never received sync-document`);
      return last.state;
    },
    async gaugeValue(name) {
      const metric = await metrics[name].get();
      return metric.values[0]?.value ?? 0;
    },
    async counterValue(name, labels) {
      const metric = await metrics[name].get();
      const match = metric.values.find((v) =>
        labels ? Object.entries(labels).every(([k, val]) => v.labels[k] === val) : true,
      );
      return match?.value ?? 0;
    },
    async stop() {
      await manager.stop();
    },
  };
}
