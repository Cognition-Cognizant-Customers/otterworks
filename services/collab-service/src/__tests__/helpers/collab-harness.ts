import { Server as SocketIOServer, Socket as ServerSocket } from 'socket.io';
import { createServer, Server as HttpServer } from 'http';
import { io as clientIO, Socket as ClientSocket } from 'socket.io-client';
import jwt from 'jsonwebtoken';
import type { Logger } from 'pino';
import * as Y from 'yjs';
import { CollaborationManager } from '../../handlers/collaboration';
import { DocumentStore } from '../../services/document-store';
import { AwarenessService } from '../../services/awareness';
import { PresenceHandler } from '../../handlers/presence';
import { MetricsCollector } from '../../metrics';
import { createAuthMiddleware } from '../../middleware/auth';
import { RedisAdapter } from '../../services/redis-adapter';

/**
 * Shared harness for the WP-11 socket-level collaboration tests.
 *
 * It stands up the real Socket.IO server, auth middleware and
 * CollaborationManager against a fully mocked Redis, so the suite needs no
 * infrastructure. Nothing here waits on wall time.
 */

export const JWT_SECRET = 'test-secret-key-for-wp11';

export type MockRedis = jest.Mocked<RedisAdapter>;

export function createMockRedis(): MockRedis {
  return {
    get: jest.fn().mockResolvedValue(null),
    set: jest.fn().mockResolvedValue(undefined),
    del: jest.fn().mockResolvedValue(undefined),
    hset: jest.fn().mockResolvedValue(undefined),
    hget: jest.fn().mockResolvedValue(null),
    hgetall: jest.fn().mockResolvedValue({}),
    hdel: jest.fn().mockResolvedValue(undefined),
    hincrby: jest.fn().mockResolvedValue(1),
    lpush: jest.fn().mockResolvedValue(undefined),
    lrange: jest.fn().mockResolvedValue([]),
    ltrim: jest.fn().mockResolvedValue(undefined),
    llen: jest.fn().mockResolvedValue(0),
    expire: jest.fn().mockResolvedValue(undefined),
    publish: jest.fn().mockResolvedValue(undefined),
    subscribe: jest.fn().mockResolvedValue(undefined),
    connect: jest.fn().mockResolvedValue(undefined),
    disconnect: jest.fn(),
    ping: jest.fn().mockResolvedValue(true),
  } as unknown as MockRedis;
}

export type MockLogger = Logger & {
  info: jest.Mock;
  warn: jest.Mock;
  error: jest.Mock;
  debug: jest.Mock;
};

export function createMockLogger(): MockLogger {
  return {
    info: jest.fn(),
    warn: jest.fn(),
    error: jest.fn(),
    debug: jest.fn(),
    fatal: jest.fn(),
    trace: jest.fn(),
    child: jest.fn().mockReturnThis(),
    level: 'info',
  } as unknown as MockLogger;
}

export interface Harness {
  io: SocketIOServer;
  httpServer: HttpServer;
  manager: CollaborationManager;
  awareness: AwarenessService;
  documentStore: DocumentStore;
  redis: MockRedis;
  logger: MockLogger;
  port: number;
  serverSockets: Map<string, ServerSocket>;
}

export async function startHarness(): Promise<Harness> {
  const redis = createMockRedis();
  const logger = createMockLogger();
  const httpServer = createServer();
  // Mirrors src/index.ts: no explicit maxHttpBufferSize, so the effective
  // payload cap is Socket.IO's 1e6-byte default.
  const io = new SocketIOServer(httpServer, { cors: { origin: '*' } });
  io.use(createAuthMiddleware(JWT_SECRET, logger));

  const awareness = new AwarenessService(logger);
  const documentStore = new DocumentStore(redis, logger);
  const manager = new CollaborationManager({
    io,
    documentStore,
    awareness,
    presenceHandler: new PresenceHandler(awareness, logger),
    metrics: new MetricsCollector(),
    logger,
    // Long intervals: the periodic loops must never fire during a test.
    persistIntervalMs: 3_600_000,
    snapshotIntervalMs: 3_600_000,
  });
  manager.start();

  const serverSockets = new Map<string, ServerSocket>();
  io.on('connection', (socket) => {
    serverSockets.set(socket.id, socket);
    socket.on('disconnect', () => serverSockets.delete(socket.id));
  });

  await new Promise<void>((resolve) => httpServer.listen(0, resolve));
  const address = httpServer.address();
  const port = typeof address === 'object' && address ? address.port : 0;

  return {
    io,
    httpServer,
    manager,
    awareness,
    documentStore,
    redis,
    logger,
    port,
    serverSockets,
  };
}

export async function stopHarness(harness: Harness): Promise<void> {
  await harness.manager.stop();
  harness.io.close();
  await new Promise<void>((resolve) => harness.httpServer.close(() => resolve()));
}

export function signToken(payload: Record<string, unknown>, secret = JWT_SECRET): string {
  return jwt.sign(payload, secret); // nosemgrep: javascript.jsonwebtoken.security.jwt-hardcode.hardcoded-jwt-secret
}

export function userToken(userId: string): string {
  return signToken({
    sub: userId,
    name: userId,
    email: `${userId}@test.com`,
    roles: ['user'],
    exp: Math.floor(Date.now() / 1000) + 3600,
  });
}

export function connectWithToken(
  harness: Harness,
  token: string | undefined,
): Promise<ClientSocket> {
  return new Promise((resolve, reject) => {
    const client = clientIO(`http://localhost:${harness.port}`, {
      auth: token === undefined ? {} : { token },
      transports: ['websocket'],
      reconnection: false,
    });
    client.on('connect', () => resolve(client));
    client.on('connect_error', (err) => reject(err));
  });
}

export function connectUser(harness: Harness, userId: string): Promise<ClientSocket> {
  return connectWithToken(harness, userToken(userId));
}

export function joinDocument(
  client: ClientSocket,
  documentId: string,
): Promise<{ success: boolean; error?: string }> {
  return new Promise((resolve) => {
    client.emit('join-document', { documentId }, resolve);
  });
}

/**
 * Round-trips a read-only request. Because Socket.IO preserves per-connection
 * ordering and the update handler applies its CRDT update synchronously, the
 * reply proves every previously emitted packet has already been handled.
 */
export function barrier(client: ClientSocket, documentId: string): Promise<void> {
  return new Promise((resolve) => {
    client.once('document-history', () => resolve());
    client.emit('request-history', { documentId });
  });
}

export function nextEvent<T>(client: ClientSocket, event: string): Promise<T> {
  return new Promise((resolve) => client.once(event, resolve));
}

/**
 * Resolves once the server has processed the disconnect for `socketId`.
 * The listener is attached after CollaborationManager's own handler, so it
 * fires strictly afterwards.
 */
export function serverDisconnect(harness: Harness, socketId: string): Promise<void> {
  const socket = harness.serverSockets.get(socketId);
  if (!socket) return Promise.resolve();
  return new Promise((resolve) => socket.on('disconnect', () => resolve()));
}

/**
 * Drains pending microtasks by yielding to the event loop a bounded number of
 * times. Used after a disconnect to let the (fully mocked, immediately
 * resolved) persistence chain settle. This is not a timed wait.
 */
export async function drainAsyncWork(ticks = 5): Promise<void> {
  for (let i = 0; i < ticks; i++) {
    await new Promise<void>((resolve) => setImmediate(resolve));
  }
}

/**
 * Disconnects every client and waits for the server to finish handling each
 * disconnect, so no test inherits documents or awareness entries from the
 * previous one.
 */
export async function disconnectClients(
  harness: Harness,
  clients: ClientSocket[],
): Promise<void> {
  const settled = clients
    .filter((client) => client.connected && client.id)
    .map((client) => {
      const done = serverDisconnect(harness, client.id as string);
      client.disconnect();
      return done;
    });
  for (const client of clients) client.disconnect();
  await Promise.all(settled);
  await drainAsyncWork();
}

export function base64Update(mutate: (doc: Y.Doc) => void): string {
  const doc = new Y.Doc();
  mutate(doc);
  return Buffer.from(Y.encodeStateAsUpdate(doc)).toString('base64');
}

/** Encoded Socket.IO packet length for `socket.emit(event, payload)`. */
export function packetLength(event: string, payload: unknown): number {
  return 2 + JSON.stringify([event, payload]).length;
}
