import { Socket as ClientSocket } from 'socket.io-client';
import * as Y from 'yjs';
import {
  Harness,
  barrier,
  base64Update,
  connectUser,
  connectWithToken,
  disconnectClients,
  joinDocument,
  nextEvent,
  packetLength,
  signToken,
  startHarness,
  stopHarness,
} from './helpers/collab-harness';

/**
 * WP-11 — room/document lifecycle, authorization negatives and malformed
 * protocol messages.
 *
 * Tests whose name contains `currently` pin behaviour that looks wrong but is
 * production behaviour today; each has a matching `it.skip` describing what the
 * service should do instead. Fixing the defect is deliberately out of scope for
 * a coverage package (SOW §7.2) — the skipped test is the finding's receipt.
 *
 * Determinism: acknowledgements and server events sequence everything; nothing
 * sleeps.
 */

const MAX_PACKET_BYTES = 1_000_000; // Socket.IO's maxHttpBufferSize default

describe('Collaboration protocol edges (WP-11)', () => {
  let harness: Harness;
  let clients: ClientSocket[];

  beforeAll(async () => {
    harness = await startHarness();
  }, 20000);

  afterAll(async () => {
    await stopHarness(harness);
  });

  beforeEach(() => {
    clients = [];
  });

  afterEach(async () => {
    // Waiting for the server side of every disconnect keeps document and
    // awareness state from leaking into the next test.
    await disconnectClients(harness, clients);
    jest.clearAllMocks();
  });

  async function connect(userId: string): Promise<ClientSocket> {
    const client = await connectUser(harness, userId);
    clients.push(client);
    return client;
  }

  describe('handshake negatives', () => {
    it('rejects a token signed with the wrong secret', async () => {
      await expect(
        connectWithToken(harness, signToken({ sub: 'mallory' }, 'not-the-real-secret')),
      ).rejects.toThrow('Invalid or expired token');
    });

    it('rejects an expired token', async () => {
      const expired = signToken({
        sub: 'user-expired',
        iat: 1_600_000_000,
        exp: 1_600_003_600,
      });

      await expect(connectWithToken(harness, expired)).rejects.toThrow(
        'Invalid or expired token',
      );
    });

    it('rejects an unsigned (alg: none) token', async () => {
      const header = Buffer.from(JSON.stringify({ alg: 'none', typ: 'JWT' })).toString(
        'base64url',
      );
      const body = Buffer.from(JSON.stringify({ sub: 'mallory' })).toString('base64url');

      await expect(connectWithToken(harness, `${header}.${body}.`)).rejects.toThrow(
        'Invalid or expired token',
      );
    });

    it('rejects an empty-string token as a missing token', async () => {
      await expect(connectWithToken(harness, '')).rejects.toThrow(
        'Authentication required',
      );
    });

    it('accepts a valid token carried in the Authorization header instead of auth.token', async () => {
      const { io: clientIO } = await import('socket.io-client');
      const client = clientIO(`http://localhost:${harness.port}`, {
        transports: ['websocket'],
        reconnection: false,
        extraHeaders: { authorization: `Bearer ${signToken({ sub: 'header-user' })}` },
      });
      clients.push(client);

      await new Promise<void>((resolve, reject) => {
        client.on('connect', () => resolve());
        client.on('connect_error', reject);
      });
      expect(client.connected).toBe(true);
    });

    it('currently accepts a token with no `sub`, yielding an undefined user id', async () => {
      // FINDING F5: `sub` is never validated; the resulting awareness entry has
      // `userId: undefined`, which propagates into presence and comment authors.
      const client = await connectWithToken(harness, signToken({ email: 'x@test.com' }));
      clients.push(client);
      const peer = await connect('user-sub-peer');
      await joinDocument(peer, 'doc-no-sub');

      const joined = nextEvent<{ userId?: string }>(peer, 'user-joined');
      await joinDocument(client, 'doc-no-sub');

      expect((await joined).userId).toBeUndefined();
    });

    it.skip('should reject a token without a `sub` claim (FINDING F5)', async () => {
      await expect(
        connectWithToken(harness, signToken({ email: 'x@test.com' })),
      ).rejects.toThrow(/token/i);
    });
  });

  describe('room lifecycle', () => {
    it('creates a document on first join rather than rejecting an unknown id', async () => {
      const client = await connect('user-life-1');

      const ack = await joinDocument(client, 'doc-never-seen-before');

      expect(ack).toEqual({ success: true });
      expect(harness.manager.getDocument('doc-never-seen-before')).toBeDefined();
    });

    it('joining the same document twice keeps a single awareness entry', async () => {
      const client = await connect('user-life-2');

      await joinDocument(client, 'doc-rejoin');
      await joinDocument(client, 'doc-rejoin');

      expect(harness.awareness.getDocumentUserCount('doc-rejoin')).toBe(1);
    });

    it('switching documents leaves the previous room and evicts the emptied document', async () => {
      const client = await connect('user-life-3');
      await joinDocument(client, 'doc-switch-from');
      await joinDocument(client, 'doc-switch-to');

      expect(harness.awareness.getDocumentUserCount('doc-switch-from')).toBe(0);
      expect(harness.awareness.getUserDocument(client.id!)).toBe('doc-switch-to');
      expect(harness.manager.getDocument('doc-switch-from')).toBeUndefined();
    });

    it('leaving a document a client never joined is a no-op', async () => {
      const observer = await connect('user-life-4a');
      const stranger = await connect('user-life-4b');
      await joinDocument(observer, 'doc-leave-noop');

      const events: unknown[] = [];
      observer.on('user-left', (payload) => events.push(payload));
      stranger.emit('leave-document', { documentId: 'doc-leave-noop' });
      await barrier(observer, 'doc-leave-noop');

      expect(events).toEqual([]);
      expect(harness.awareness.getDocumentUserCount('doc-leave-noop')).toBe(1);
    });

    it('uses the tracked document when the client sends the wrong id on leave', async () => {
      const alice = await connect('user-life-5a');
      const bob = await connect('user-life-5b');
      await joinDocument(alice, 'doc-tracked');
      await joinDocument(bob, 'doc-tracked');

      const left = nextEvent<{ userId: string }>(alice, 'user-left');
      bob.emit('leave-document', { documentId: 'doc-some-other-room' });

      expect((await left).userId).toBe('user-life-5b');
      expect(harness.awareness.getDocumentUserCount('doc-tracked')).toBe(1);
    });

    it('currently enforces no maximum number of clients per document', async () => {
      // FINDING F4: there is no max-clients-per-doc cap, so a single document
      // can fan out unboundedly. 8 is arbitrary; nothing rejects the Nth join.
      const joined = await Promise.all(
        Array.from({ length: 8 }, async (_, i) => {
          const client = await connect(`user-cap-${i}`);
          return joinDocument(client, 'doc-capacity');
        }),
      );

      expect(joined.every((ack) => ack.success)).toBe(true);
      expect(harness.awareness.getDocumentUserCount('doc-capacity')).toBe(8);
    });

    it.skip('should reject the join that exceeds a max-clients-per-document cap (FINDING F4)', async () => {
      // No cap exists in config.ts, so there is no limit-1 / limit / limit+1
      // trio to assert. Unskip once a cap is introduced.
      expect(true).toBe(false);
    });

    it('currently accepts an empty document id and creates a room for it', async () => {
      const client = await connect('user-life-6');

      const ack = await joinDocument(client, '');

      expect(ack).toEqual({ success: true });
      expect(harness.manager.getDocument('')).toBeDefined();
    });
  });

  describe('authorization negatives', () => {
    it('currently lets any authenticated user join a document created by another user', async () => {
      // FINDING F1: join-document performs no ownership or ACL check. Document
      // ownership lives in document-service and is never consulted here.
      const owner = await connect('user-owner');
      await joinDocument(owner, 'doc-owned-by-someone-else');

      const stranger = await connect('user-stranger');
      const ack = await joinDocument(stranger, 'doc-owned-by-someone-else');

      expect(ack).toEqual({ success: true });
      expect(harness.awareness.getDocumentUserCount('doc-owned-by-someone-else')).toBe(2);
    });

    it.skip('should refuse a join for a document the user cannot access (FINDING F1)', async () => {
      const stranger = await connect('user-stranger-denied');

      const ack = await joinDocument(stranger, 'doc-owned-by-someone-else');

      expect(ack.success).toBe(false);
      expect(ack.error).toMatch(/forbidden|not authorized/i);
    });

    it('currently relays a cursor update into a room the sender never joined', async () => {
      // FINDING F2: handleCursorUpdate broadcasts to `doc:${data.documentId}`
      // taken straight from the client payload, with no membership check, so a
      // member of one document can inject presence into any other document.
      const insider = await connect('user-authz-1a');
      const outsider = await connect('user-authz-1b');
      await joinDocument(insider, 'doc-private');
      await joinDocument(outsider, 'doc-elsewhere');

      const leaked = nextEvent<{ userId: string }>(insider, 'cursor-update');
      outsider.emit('cursor-update', {
        documentId: 'doc-private',
        cursor: { index: 0, length: 0 },
        selection: null,
      });

      expect((await leaked).userId).toBe('user-authz-1b');
    });

    it.skip('should drop a cursor update for a document the sender is not in (FINDING F2)', async () => {
      const insider = await connect('user-authz-2a');
      const outsider = await connect('user-authz-2b');
      await joinDocument(insider, 'doc-private-2');
      await joinDocument(outsider, 'doc-elsewhere-2');

      const seen: unknown[] = [];
      insider.on('cursor-update', (payload) => seen.push(payload));
      outsider.emit('cursor-update', {
        documentId: 'doc-private-2',
        cursor: { index: 0, length: 0 },
        selection: null,
      });
      await barrier(insider, 'doc-private-2');

      expect(seen).toEqual([]);
    });

    it('currently relays a typing indicator into a room the sender never joined', async () => {
      // FINDING F2 (same root cause as the cursor case).
      const insider = await connect('user-authz-3a');
      const outsider = await connect('user-authz-3b');
      await joinDocument(insider, 'doc-typing-private');
      await joinDocument(outsider, 'doc-typing-elsewhere');

      const leaked = nextEvent<{ userId: string; isTyping: boolean }>(
        insider,
        'typing-indicator',
      );
      outsider.emit('typing-indicator', {
        documentId: 'doc-typing-private',
        isTyping: true,
      });

      expect(await leaked).toMatchObject({ userId: 'user-authz-3b', isTyping: true });
    });

    it('currently relays a comment into a room the sender never joined', async () => {
      // FINDING F2 (comment-add/update/delete all trust the payload's id).
      const insider = await connect('user-authz-4a');
      const outsider = await connect('user-authz-4b');
      await joinDocument(insider, 'doc-comment-private');
      await joinDocument(outsider, 'doc-comment-elsewhere');

      const leaked = nextEvent<{ author: { userId: string } }>(insider, 'comment-added');
      outsider.emit('comment-add', {
        documentId: 'doc-comment-private',
        comment: {
          id: 'c1',
          threadId: 't1',
          content: 'injected',
          rangeStart: 0,
          rangeEnd: 1,
        },
      });

      expect((await leaked).author.userId).toBe('user-authz-4b');
    });

    it('currently applies a document update from a client that never joined the document', async () => {
      // FINDING F3: handleDocumentUpdate only checks that the document is in
      // memory, never that this socket is a participant. Any authenticated
      // client can mutate any open document.
      const member = await connect('user-authz-5a');
      const outsider = await connect('user-authz-5b');
      await joinDocument(member, 'doc-writable-by-anyone');
      await joinDocument(outsider, 'doc-outsider-home');

      outsider.emit('document-update', {
        documentId: 'doc-writable-by-anyone',
        update: base64Update((doc) => doc.getText('content').insert(0, 'injected')),
      });
      await barrier(member, 'doc-writable-by-anyone');

      expect(
        harness.manager
          .getDocument('doc-writable-by-anyone')!
          .getText('content')
          .toString(),
      ).toBe('injected');
    });

    it.skip('should ignore a document update from a non-participant (FINDING F3)', async () => {
      const member = await connect('user-authz-6a');
      const outsider = await connect('user-authz-6b');
      await joinDocument(member, 'doc-protected');
      await joinDocument(outsider, 'doc-outsider-home-2');

      outsider.emit('document-update', {
        documentId: 'doc-protected',
        update: base64Update((doc) => doc.getText('content').insert(0, 'injected')),
      });
      await barrier(member, 'doc-protected');

      expect(
        harness.manager.getDocument('doc-protected')!.getText('content').toString(),
      ).toBe('');
    });

    it('derives the comment author from the JWT and ignores a spoofed author in the payload', async () => {
      const alice = await connect('user-authz-7a');
      const bob = await connect('user-authz-7b');
      await joinDocument(alice, 'doc-comment-authz');
      await joinDocument(bob, 'doc-comment-authz');

      const received = nextEvent<{ author: { userId: string; displayName: string } }>(
        alice,
        'comment-added',
      );
      bob.emit('comment-add', {
        documentId: 'doc-comment-authz',
        comment: {
          id: 'c2',
          threadId: 't2',
          content: 'hi',
          rangeStart: 0,
          rangeEnd: 1,
          author: { userId: 'user-authz-7a', displayName: 'Impersonated' },
        },
      });

      expect((await received).author).toEqual({
        userId: 'user-authz-7b',
        displayName: 'user-authz-7b',
      });
    });

    it('does not deliver a document update to a client in a different document', async () => {
      const inRoom = await connect('user-authz-8a');
      const elsewhere = await connect('user-authz-8b');
      await joinDocument(inRoom, 'doc-isolated-a');
      await joinDocument(elsewhere, 'doc-isolated-b');

      const leaked: unknown[] = [];
      elsewhere.on('document-update', (payload) => leaked.push(payload));
      inRoom.emit('document-update', {
        documentId: 'doc-isolated-a',
        update: base64Update((doc) => doc.getText('content').insert(0, 'secret')),
      });
      await barrier(inRoom, 'doc-isolated-a');
      await barrier(elsewhere, 'doc-isolated-b');

      expect(leaked).toEqual([]);
    });

    it('does not deliver presence of one document to another document', async () => {
      const watcher = await connect('user-authz-9a');
      await joinDocument(watcher, 'doc-presence-a');
      const presenceEvents: Array<{ documentId: string }> = [];
      watcher.on('presence-update', (payload) => presenceEvents.push(payload));

      const other = await connect('user-authz-9b');
      await joinDocument(other, 'doc-presence-b');
      await barrier(watcher, 'doc-presence-a');

      expect(presenceEvents.every((p) => p.documentId === 'doc-presence-a')).toBe(true);
    });
  });

  describe('malformed protocol messages', () => {
    async function joinedClient(user: string, documentId: string) {
      const client = await connect(user);
      await joinDocument(client, documentId);
      return client;
    }

    it('reports an error and leaves the document untouched for a truncated update', async () => {
      const client = await joinedClient('user-mal-1', 'doc-truncated');
      const valid = Buffer.from(
        base64Update((doc) => doc.getText('content').insert(0, 'complete')),
        'base64',
      );
      const truncated = valid.subarray(0, valid.length - 3).toString('base64');

      const error = nextEvent<{ documentId: string; error: string }>(
        client,
        'document-update-error',
      );
      client.emit('document-update', { documentId: 'doc-truncated', update: truncated });

      expect(await error).toEqual({
        documentId: 'doc-truncated',
        error: 'Failed to apply update',
      });
      expect(
        harness.manager.getDocument('doc-truncated')!.getText('content').toString(),
      ).toBe('');
    });

    it('reports an error for a non-base64 garbage update', async () => {
      const client = await joinedClient('user-mal-2', 'doc-garbage');

      const error = nextEvent<{ error: string }>(client, 'document-update-error');
      client.emit('document-update', {
        documentId: 'doc-garbage',
        update: 'not-a-real-update-!!!',
      });

      expect((await error).error).toBe('Failed to apply update');
    });

    it('reports an error for an empty update string', async () => {
      const client = await joinedClient('user-mal-3', 'doc-empty-update');

      const error = nextEvent<{ error: string }>(client, 'document-update-error');
      client.emit('document-update', { documentId: 'doc-empty-update', update: '' });

      expect((await error).error).toBe('Failed to apply update');
    });

    it('does not broadcast an update that failed to apply', async () => {
      const author = await joinedClient('user-mal-4a', 'doc-no-broadcast');
      const peer = await connect('user-mal-4b');
      await joinDocument(peer, 'doc-no-broadcast');

      const seen: unknown[] = [];
      peer.on('document-update', (payload) => seen.push(payload));
      author.emit('document-update', {
        documentId: 'doc-no-broadcast',
        update: 'garbage',
      });
      await nextEvent(author, 'document-update-error');
      await barrier(peer, 'doc-no-broadcast');

      expect(seen).toEqual([]);
    });

    it('ignores an update addressed to a document that is not in memory', async () => {
      const client = await joinedClient('user-mal-5', 'doc-in-memory');

      const errors: unknown[] = [];
      client.on('document-update-error', (payload) => errors.push(payload));
      client.emit('document-update', {
        documentId: 'doc-not-in-memory',
        update: base64Update((doc) => doc.getText('content').insert(0, 'x')),
      });
      await barrier(client, 'doc-in-memory');

      expect(errors).toEqual([]);
      expect(harness.manager.getDocument('doc-not-in-memory')).toBeUndefined();
    });

    it('currently broadcasts a non-string update without applying it to the CRDT', async () => {
      // FINDING F6: the handler only decodes `typeof update === 'string'`
      // (legacy JSON patch support), but still relays anything else to peers,
      // so a client can broadcast a payload no peer can merge.
      const author = await joinedClient('user-mal-6a', 'doc-non-string');
      const peer = await connect('user-mal-6b');
      await joinDocument(peer, 'doc-non-string');

      const relayed = nextEvent<{ update: unknown }>(peer, 'document-update');
      author.emit('document-update', {
        documentId: 'doc-non-string',
        update: { op: 'replace', path: '/title', value: 'legacy patch' },
      });

      expect((await relayed).update).toEqual({
        op: 'replace',
        path: '/title',
        value: 'legacy patch',
      });
      expect(
        harness.manager.getDocument('doc-non-string')!.getText('content').toString(),
      ).toBe('');
    });

    it('ignores an unknown event type and keeps the connection usable', async () => {
      const client = await joinedClient('user-mal-7', 'doc-unknown-event');

      client.emit('totally-unknown-event', { documentId: 'doc-unknown-event' });
      await barrier(client, 'doc-unknown-event');

      expect(client.connected).toBe(true);
    });

    it('ignores a cursor update from a socket that has not joined any document', async () => {
      const lurker = await connect('user-mal-8a');
      const member = await connect('user-mal-8b');
      await joinDocument(member, 'doc-lurker');

      const seen: unknown[] = [];
      member.on('cursor-update', (payload) => seen.push(payload));
      lurker.emit('cursor-update', {
        documentId: 'doc-lurker',
        cursor: { index: 1, length: 0 },
        selection: null,
      });
      await barrier(member, 'doc-lurker');

      expect(seen).toEqual([]);
    });

    it.skip('crashes the process on a null join-document payload (FINDING F7)', async () => {
      // `handleJoinDocument` destructures `data.documentId` outside its
      // try/catch, so `socket.emit('join-document')` with no payload raises an
      // uncaught TypeError in the server process. Reproduced out-of-band; kept
      // skipped because an uncaught exception would abort this worker.
      const client = await connect('user-mal-9');

      client.emit('join-document');

      expect(client.connected).toBe(true);
    });
  });

  describe('payload size boundary (Socket.IO maxHttpBufferSize = 1e6)', () => {
    function updateOfExactPacketSize(documentId: string, targetBytes: number) {
      const overhead = packetLength('document-update', { documentId, update: '' });
      return 'a'.repeat(targetBytes - overhead);
    }

    async function sendSized(
      documentId: string,
      user: string,
      targetBytes: number,
    ): Promise<'applied-error' | 'disconnected'> {
      const client = await connect(user);
      await joinDocument(client, documentId);
      const update = updateOfExactPacketSize(documentId, targetBytes);
      expect(packetLength('document-update', { documentId, update })).toBe(targetBytes);

      const outcome = new Promise<'applied-error' | 'disconnected'>((resolve) => {
        client.once('document-update-error', () => resolve('applied-error'));
        client.once('disconnect', () => resolve('disconnected'));
      });
      client.emit('document-update', { documentId, update });
      return outcome;
    }

    it('accepts a packet of cap-1 bytes (limit-1)', async () => {
      await expect(
        sendSized('doc-size-under', 'user-size-1', MAX_PACKET_BYTES - 1),
      ).resolves.toBe('applied-error');
    });

    it('accepts a packet of exactly cap bytes (limit)', async () => {
      await expect(
        sendSized('doc-size-exact', 'user-size-2', MAX_PACKET_BYTES),
      ).resolves.toBe('applied-error');
    });

    it('closes the connection for a packet of cap+1 bytes (limit+1)', async () => {
      await expect(
        sendSized('doc-size-over', 'user-size-3', MAX_PACKET_BYTES + 1),
      ).resolves.toBe('disconnected');
    });

    it('applies a large but valid update below the cap', async () => {
      const client = await connect('user-size-4');
      await joinDocument(client, 'doc-size-valid');
      const big = 'x'.repeat(100_000);

      client.emit('document-update', {
        documentId: 'doc-size-valid',
        update: base64Update((doc) => doc.getText('content').insert(0, big)),
      });
      await barrier(client, 'doc-size-valid');

      expect(
        harness.manager.getDocument('doc-size-valid')!.getText('content').toString(),
      ).toHaveLength(100_000);
    });
  });

  describe('document state accounting', () => {
    it('counts each open document exactly once regardless of participant count', async () => {
      const before = harness.manager.getDocumentCount();
      const a = await connect('user-count-1a');
      const b = await connect('user-count-1b');
      const c = await connect('user-count-1c');
      await joinDocument(a, 'doc-count-1');
      await joinDocument(b, 'doc-count-1');
      await joinDocument(c, 'doc-count-2');

      expect(harness.manager.getDocumentCount()).toBe(before + 2);
    });

    it('exposes the same Y.Doc instance for every participant', async () => {
      const a = await connect('user-count-2a');
      const b = await connect('user-count-2b');
      await joinDocument(a, 'doc-shared-instance');
      await joinDocument(b, 'doc-shared-instance');

      const doc = harness.manager.getDocument('doc-shared-instance');
      expect(doc).toBeInstanceOf(Y.Doc);
      expect(harness.manager.getDocument('doc-shared-instance')).toBe(doc);
    });
  });
});
