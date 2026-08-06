import { createServer, type Server as HttpServer } from 'http';
import { Server as SocketIOServer } from 'socket.io';
import { io as clientIO, type Socket as ClientSocket } from 'socket.io-client';
import jwt from 'jsonwebtoken';
import { CollaborationManager } from '../handlers/collaboration';
import { PresenceHandler } from '../handlers/presence';
import { AwarenessService } from '../services/awareness';
import { DocumentStore } from '../services/document-store';
import { MetricsCollector } from '../metrics';
import { createAuthMiddleware } from '../middleware/auth';
import { InMemoryRedis } from './helpers/in-memory-redis';
import { createTestLogger } from './helpers/test-logger';
import { createReplica, garbageUpdateB64 } from './helpers/yjs';

const JWT_SECRET = 'wp11-transport-secret'; // nosemgrep: javascript.jsonwebtoken.security.jwt-hardcode.hardcoded-jwt-secret
const MAX_BUFFER_BYTES = 8192;

/**
 * Transport-level negatives over a real socket.io server: token rejection paths and the
 * `maxHttpBufferSize` frame boundary. Everything is event-driven — no timed waits.
 */
describe('Socket transport', () => {
  let httpServer: HttpServer;
  let io: SocketIOServer;
  let manager: CollaborationManager;
  let port: number;
  const clients: ClientSocket[] = [];

  beforeAll(async () => {
    const logger = createTestLogger();
    httpServer = createServer();
    io = new SocketIOServer(httpServer, {
      cors: { origin: '*' },
      maxHttpBufferSize: MAX_BUFFER_BYTES,
    });
    io.use(createAuthMiddleware(JWT_SECRET, logger.asLogger()));

    const awareness = new AwarenessService(logger.asLogger());
    manager = new CollaborationManager({
      io,
      documentStore: new DocumentStore(
        new InMemoryRedis().asAdapter(),
        logger.asLogger(),
      ),
      awareness,
      presenceHandler: new PresenceHandler(awareness, logger.asLogger()),
      metrics: new MetricsCollector(),
      logger: logger.asLogger(),
      persistIntervalMs: 3_600_000,
      snapshotIntervalMs: 3_600_000,
    });
    manager.start();

    await new Promise<void>((resolve) => {
      httpServer.listen(0, () => {
        const address = httpServer.address();
        port = typeof address === 'object' && address ? address.port : 0;
        resolve();
      });
    });
  });

  afterEach(() => {
    while (clients.length > 0) clients.pop()?.disconnect();
  });

  afterAll(async () => {
    await manager.stop();
    io.close();
    await new Promise<void>((resolve) => httpServer.close(() => resolve()));
  });

  function token(payload: Record<string, unknown>, expiresInSeconds = 3600): string {
    return jwt.sign(payload, JWT_SECRET, { expiresIn: expiresInSeconds }); // nosemgrep: javascript.jsonwebtoken.security.jwt-hardcode.hardcoded-jwt-secret
  }

  function open(auth: Record<string, unknown>, headers?: Record<string, string>) {
    const client = clientIO(`http://localhost:${port}`, {
      auth,
      extraHeaders: headers,
      transports: ['websocket'],
      reconnection: false,
    });
    clients.push(client);
    return client;
  }

  function connected(client: ClientSocket): Promise<ClientSocket> {
    return new Promise((resolve, reject) => {
      client.on('connect', () => resolve(client));
      client.on('connect_error', reject);
    });
  }

  function rejected(client: ClientSocket): Promise<Error> {
    return new Promise((resolve, reject) => {
      client.on('connect_error', (err: Error) => resolve(err));
      client.on('connect', () => reject(new Error('connection unexpectedly succeeded')));
    });
  }

  function once<T>(client: ClientSocket, event: string): Promise<T> {
    return new Promise((resolve) => client.once(event, (data: T) => resolve(data)));
  }

  function join(client: ClientSocket, documentId: string): Promise<{ success: boolean }> {
    return new Promise((resolve) => {
      client.emit('join-document', { documentId }, resolve);
    });
  }

  describe('handshake rejection', () => {
    it('rejects an expired token', async () => {
      const expired = token({ sub: 'user-expired' }, -1);

      const err = await rejected(open({ token: expired }));

      expect(err.message).toBe('Invalid or expired token');
    });

    it('rejects a token signed with another secret', async () => {
      const foreign = jwt.sign({ sub: 'user-foreign' }, 'not-the-server-secret'); // nosemgrep: javascript.jsonwebtoken.security.jwt-hardcode.hardcoded-jwt-secret

      const err = await rejected(open({ token: foreign }));

      expect(err.message).toBe('Invalid or expired token');
    });

    it('rejects an unsigned alg:none token', async () => {
      const header = Buffer.from(JSON.stringify({ alg: 'none', typ: 'JWT' })).toString(
        'base64url',
      );
      const body = Buffer.from(JSON.stringify({ sub: 'user-none' })).toString(
        'base64url',
      );

      const err = await rejected(open({ token: `${header}.${body}.` }));

      expect(err.message).toBe('Invalid or expired token');
    });

    it('rejects an empty token string', async () => {
      const err = await rejected(open({ token: '' }));

      expect(err.message).toBe('Authentication required');
    });

    it('rejects an Authorization header that carries no credentials', async () => {
      // The scheme without a token is not treated as "no token": `replace('Bearer ', '')`
      // leaves a non-empty string, so it reaches jwt.verify and fails there instead.
      const err = await rejected(open({}, { authorization: 'Bearer' }));

      expect(err.message).toBe('Invalid or expired token');
    });

    it('rejects a non-bearer Authorization scheme', async () => {
      const err = await rejected(
        open({}, { authorization: `Token ${token({ sub: 'user-scheme' })}` }),
      );

      expect(err.message).toBe('Invalid or expired token');
    });
  });

  describe('identity resolution', () => {
    it('accepts a token supplied in the Authorization header', async () => {
      const client = await connected(
        open({}, { authorization: `Bearer ${token({ sub: 'user-header' })}` }),
      );

      expect(client.connected).toBe(true);
    });

    it('accepts a bare header token with no Bearer scheme', async () => {
      // FINDING (genuine, low severity): the scheme is stripped with an unanchored
      // `replace`, so an Authorization header without any scheme authenticates too.
      const client = await connected(
        open({}, { authorization: token({ sub: 'user-bare' }) }),
      );

      expect(client.connected).toBe(true);
    });

    it('falls back to Anonymous when the token carries no display name', async () => {
      const client = await connected(open({ token: token({ sub: 'user-nameless' }) }));
      await join(client, 'doc-nameless');
      const added = once<{ author: { displayName: string; userId: string } }>(
        client,
        'comment-added',
      );

      client.emit('comment-add', {
        documentId: 'doc-nameless',
        comment: { id: 'c1', threadId: 't1', content: 'hi', rangeStart: 0, rangeEnd: 1 },
      });

      expect((await added).author).toEqual({
        userId: 'user-nameless',
        displayName: 'Anonymous',
      });
    });

    it('accepts display_name as an alternative to name', async () => {
      const client = await connected(
        open({ token: token({ sub: 'user-snake', display_name: 'Snake Case' }) }),
      );
      await join(client, 'doc-snake');
      const added = once<{ author: { displayName: string } }>(client, 'comment-added');

      client.emit('comment-add', {
        documentId: 'doc-snake',
        comment: { id: 'c1', threadId: 't1', content: 'hi', rangeStart: 0, rangeEnd: 1 },
      });

      expect((await added).author.displayName).toBe('Snake Case');
    });
  });

  describe('maxHttpBufferSize boundary', () => {
    const DOC = 'doc-buffer';

    /** Pad an update so the encoded socket.io frame is exactly `totalBytes` long. */
    function updateOfFrameSize(documentId: string, totalBytes: number): string {
      const overhead =
        2 + JSON.stringify(['document-update', { documentId, update: '' }]).length;
      const padding = totalBytes - overhead;
      if (padding < 0) throw new Error(`frame overhead already exceeds ${totalBytes}`);
      return 'A'.repeat(padding);
    }

    it.each([
      ['limit-1', MAX_BUFFER_BYTES - 1],
      ['limit', MAX_BUFFER_BYTES],
    ])('delivers a frame of %s (%i bytes)', async (_label, frameBytes) => {
      const sender = await connected(open({ token: token({ sub: 'user-buf' }) }));
      const observer = await connected(open({ token: token({ sub: 'user-watch' }) }));
      await Promise.all([join(sender, DOC), join(observer, DOC)]);
      const relayed = once<{ update: string }>(observer, 'document-update');
      const payload = updateOfFrameSize(DOC, frameBytes);

      sender.emit('document-update', { documentId: DOC, update: payload });

      // Seeing the relay proves the frame crossed the transport intact rather than
      // being dropped by the size guard.
      expect((await relayed).update).toBe(payload);
      expect(sender.connected).toBe(true);
    });

    it('drops the connection for a frame of limit+1 bytes', async () => {
      const sender = await connected(open({ token: token({ sub: 'user-toobig' }) }));
      const observer = await connected(open({ token: token({ sub: 'user-watch2' }) }));
      await Promise.all([join(sender, DOC), join(observer, DOC)]);
      let relayed = false;
      observer.on('document-update', () => {
        relayed = true;
      });
      const closed = once<string>(sender, 'disconnect');

      sender.emit('document-update', {
        documentId: DOC,
        update: updateOfFrameSize(DOC, MAX_BUFFER_BYTES + 1),
      });

      await closed;
      expect(sender.connected).toBe(false);
      expect(relayed).toBe(false);
    });

    it('keeps the room alive for the other members after an oversized frame', async () => {
      const room = 'doc-buffer-survives';
      const offender = await connected(open({ token: token({ sub: 'user-offender' }) }));
      const editor = await connected(open({ token: token({ sub: 'user-editor' }) }));
      const observer = await connected(open({ token: token({ sub: 'user-observer' }) }));
      await Promise.all([join(offender, room), join(editor, room), join(observer, room)]);

      const offenderClosed = once<string>(offender, 'disconnect');
      offender.emit('document-update', {
        documentId: room,
        update: updateOfFrameSize(room, MAX_BUFFER_BYTES + 1),
      });
      await offenderClosed;

      const replica = createReplica({ clientId: 99 });
      replica.text().insert(0, 'room survived');
      const relayed = once<{ update: string }>(observer, 'document-update');
      editor.emit('document-update', { documentId: room, update: replica.lastUpdate() });

      const received = createReplica();
      received.applyB64((await relayed).update);
      expect(received.read()).toBe('room survived');
      expect(editor.connected).toBe(true);
      expect(observer.connected).toBe(true);
    });

    it('reports an undecodable but in-budget payload without closing the socket', async () => {
      const client = await connected(open({ token: token({ sub: 'user-garbage' }) }));
      await join(client, 'doc-garbage');
      const rejection = once<{ documentId: string }>(client, 'document-update-error');

      client.emit('document-update', {
        documentId: 'doc-garbage',
        update: garbageUpdateB64(512),
      });

      expect((await rejection).documentId).toBe('doc-garbage');
      expect(client.connected).toBe(true);
    });
  });
});
