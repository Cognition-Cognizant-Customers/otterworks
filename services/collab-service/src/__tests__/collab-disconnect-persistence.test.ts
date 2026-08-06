import { Socket as ClientSocket } from 'socket.io-client';
import * as Y from 'yjs';
import {
  Harness,
  barrier,
  base64Update,
  connectUser,
  disconnectClients,
  drainAsyncWork,
  joinDocument,
  nextEvent,
  serverDisconnect,
  startHarness,
  stopHarness,
} from './helpers/collab-harness';

/**
 * WP-11 — disconnect edges, document eviction/persistence and the
 * snapshot/history protocol.
 *
 * Determinism: every wait is either a server event or a bounded event-loop
 * drain after such an event (Redis is mocked and resolves immediately, so the
 * remaining persistence chain is pure microtasks). No sleeps, no wall clock.
 */

const MAX_SNAPSHOTS = 50; // DocumentStore default

describe('Disconnect, persistence and history (WP-11)', () => {
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
    await disconnectClients(harness, clients);
    jest.clearAllMocks();
  });

  async function connect(userId: string): Promise<ClientSocket> {
    const client = await connectUser(harness, userId);
    clients.push(client);
    return client;
  }

  async function joined(userId: string, documentId: string): Promise<ClientSocket> {
    const client = await connect(userId);
    const ack = await joinDocument(client, documentId);
    if (!ack.success) throw new Error(ack.error ?? 'join failed');
    return client;
  }

  /** Disconnects a client and waits until the server has fully handled it. */
  async function disconnectAndSettle(client: ClientSocket): Promise<void> {
    const settled = serverDisconnect(harness, client.id as string);
    client.disconnect();
    await settled;
    await drainAsyncWork();
  }

  describe('disconnect edges', () => {
    it('keeps an update that reached the server when the sender disconnects right after', async () => {
      const author = await joined('user-disc-1a', 'doc-mid-update');
      const peer = await joined('user-disc-1b', 'doc-mid-update');
      const update = base64Update((doc) => doc.getText('content').insert(0, 'in flight'));

      const relayed = nextEvent<{ update: string }>(peer, 'document-update');
      author.emit('document-update', { documentId: 'doc-mid-update', update });
      expect((await relayed).update).toBe(update);
      await disconnectAndSettle(author);

      expect(
        harness.manager.getDocument('doc-mid-update')!.getText('content').toString(),
      ).toBe('in flight');
      expect(peer.connected).toBe(true);
    });

    it('a peer sees user-left only after the departing client\u2019s last update', async () => {
      const author = await joined('user-disc-2a', 'doc-order-on-disconnect');
      const peer = await joined('user-disc-2b', 'doc-order-on-disconnect');

      const order: string[] = [];
      const relayed = nextEvent(peer, 'document-update').then(() =>
        order.push('document-update'),
      );
      const left = nextEvent<{ userId: string }>(peer, 'user-left').then(() =>
        order.push('user-left'),
      );

      author.emit('document-update', {
        documentId: 'doc-order-on-disconnect',
        update: base64Update((doc) => doc.getText('content').insert(0, 'bye')),
      });
      await relayed;
      author.disconnect();
      await left;

      expect(order).toEqual(['document-update', 'user-left']);
    });

    it('evicts the document cleanly when the last client leaves while a persist is still in flight', async () => {
      const client = await joined('user-disc-2c', 'doc-persist-inflight');
      let releasePersist: () => void = () => undefined;
      const pending = new Promise<void>((resolve) => {
        releasePersist = resolve;
      });
      harness.redis.set.mockImplementationOnce(() => pending);

      client.emit('document-update', {
        documentId: 'doc-persist-inflight',
        update: base64Update((doc) => doc.getText('content').insert(0, 'racing')),
      });
      await barrier(client, 'doc-persist-inflight');

      const settled = serverDisconnect(harness, client.id as string);
      client.disconnect();
      await settled;
      releasePersist();
      await drainAsyncWork();

      expect(harness.manager.getDocument('doc-persist-inflight')).toBeUndefined();
      expect(harness.redis.set).toHaveBeenCalledTimes(2);
    });

    it('notifies the remaining peer and shrinks presence on disconnect', async () => {
      const stayer = await joined('user-disc-3a', 'doc-presence-shrink');
      const leaver = await joined('user-disc-3b', 'doc-presence-shrink');

      const presence = new Promise<{ count: number }>((resolve) => {
        stayer.on('presence-update', (payload: { count: number }) => {
          if (payload.count === 1) resolve(payload);
        });
      });
      const left = nextEvent<{ userId: string; socketId: string }>(stayer, 'user-left');
      leaver.disconnect();

      expect((await left).userId).toBe('user-disc-3b');
      expect((await presence).count).toBe(1);
      expect(harness.awareness.getDocumentUserCount('doc-presence-shrink')).toBe(1);
    });

    it('persists and evicts the document when the last client disconnects', async () => {
      const client = await joined('user-disc-4', 'doc-last-client');
      client.emit('document-update', {
        documentId: 'doc-last-client',
        update: base64Update((doc) => doc.getText('content').insert(0, 'final')),
      });
      await barrier(client, 'doc-last-client');
      harness.redis.set.mockClear();

      await disconnectAndSettle(client);

      expect(harness.manager.getDocument('doc-last-client')).toBeUndefined();
      expect(harness.redis.set).toHaveBeenCalledWith(
        'doc:state:doc-last-client',
        expect.any(Buffer),
        86400,
      );
      const persisted = harness.redis.set.mock.calls[0][1] as Buffer;
      const restored = new Y.Doc();
      Y.applyUpdate(restored, new Uint8Array(persisted));
      expect(restored.getText('content').toString()).toBe('final');
    });

    it('keeps the document in memory when the shutdown persist fails, so the retry loop can flush it', async () => {
      const client = await joined('user-disc-5', 'doc-persist-fails');
      harness.redis.set.mockRejectedValueOnce(new Error('redis down'));

      await disconnectAndSettle(client);

      expect(harness.manager.getDocument('doc-persist-fails')).toBeDefined();
    });

    it('does not evict a document while another participant remains', async () => {
      const stayer = await joined('user-disc-6a', 'doc-still-busy');
      const leaver = await joined('user-disc-6b', 'doc-still-busy');

      await disconnectAndSettle(leaver);

      expect(harness.manager.getDocument('doc-still-busy')).toBeDefined();
      expect(harness.awareness.getDocumentUserCount('doc-still-busy')).toBe(1);
      expect(stayer.connected).toBe(true);
    });

    it('handles the disconnect of a client that never joined a document', async () => {
      const client = await connect('user-disc-7');
      harness.redis.set.mockClear();

      await disconnectAndSettle(client);

      expect(harness.redis.set).not.toHaveBeenCalled();
    });

    it('treats leave-then-disconnect as a single departure', async () => {
      const stayer = await joined('user-disc-8a', 'doc-double-leave');
      const leaver = await joined('user-disc-8b', 'doc-double-leave');

      const departures: unknown[] = [];
      stayer.on('user-left', (payload) => departures.push(payload));
      leaver.emit('leave-document', { documentId: 'doc-double-leave' });
      await barrier(stayer, 'doc-double-leave');
      await disconnectAndSettle(leaver);
      await barrier(stayer, 'doc-double-leave');

      expect(departures).toHaveLength(1);
      expect(harness.awareness.getDocumentUserCount('doc-double-leave')).toBe(1);
    });

    it('restores the persisted state when a client reconnects to an evicted document', async () => {
      const first = await joined('user-disc-9', 'doc-reconnect');
      first.emit('document-update', {
        documentId: 'doc-reconnect',
        update: base64Update((doc) => doc.getText('content').insert(0, 'before restart')),
      });
      await barrier(first, 'doc-reconnect');
      harness.redis.set.mockClear();
      await disconnectAndSettle(first);
      const persisted = harness.redis.set.mock.calls[0][1] as Buffer;
      expect(harness.manager.getDocument('doc-reconnect')).toBeUndefined();

      harness.redis.get.mockResolvedValueOnce(persisted);
      const second = await joined('user-disc-10', 'doc-reconnect');
      await barrier(second, 'doc-reconnect');

      expect(
        harness.manager.getDocument('doc-reconnect')!.getText('content').toString(),
      ).toBe('before restart');
    });

    it('sends a reconnecting client the full merged state on re-join', async () => {
      const resident = await joined('user-disc-11a', 'doc-resync');
      resident.emit('document-update', {
        documentId: 'doc-resync',
        update: base64Update((doc) => doc.getText('content').insert(0, 'live state')),
      });
      await barrier(resident, 'doc-resync');

      const rejoiner = await connect('user-disc-11b');
      const sync = nextEvent<{ state: string }>(rejoiner, 'sync-document');
      rejoiner.emit('join-document', { documentId: 'doc-resync' }, () => {});

      const restored = new Y.Doc();
      Y.applyUpdate(restored, new Uint8Array(Buffer.from((await sync).state, 'base64')));
      expect(restored.getText('content').toString()).toBe('live state');
    });

    it('evicts the document only once when two clients disconnect together', async () => {
      const a = await joined('user-disc-12a', 'doc-concurrent-cleanup');
      const b = await joined('user-disc-12b', 'doc-concurrent-cleanup');
      harness.redis.set.mockClear();

      const settled = [
        serverDisconnect(harness, a.id as string),
        serverDisconnect(harness, b.id as string),
      ];
      a.disconnect();
      b.disconnect();
      await Promise.all(settled);
      await drainAsyncWork();

      expect(harness.manager.getDocument('doc-concurrent-cleanup')).toBeUndefined();
      expect(harness.redis.set).toHaveBeenCalledTimes(1);
    });
  });

  describe('persistence on update', () => {
    it('writes the full document state to Redis after each applied update', async () => {
      const client = await joined('user-persist-1', 'doc-persist-each');

      client.emit('document-update', {
        documentId: 'doc-persist-each',
        update: base64Update((doc) => doc.getText('content').insert(0, 'saved')),
      });
      await barrier(client, 'doc-persist-each');
      await drainAsyncWork();

      expect(harness.redis.set).toHaveBeenCalledWith(
        'doc:state:doc-persist-each',
        expect.any(Buffer),
        86400,
      );
      expect(harness.redis.hset).toHaveBeenCalledWith(
        'doc:meta:doc-persist-each',
        'lastModifiedBy',
        'user-persist-1',
      );
    });

    it('keeps serving the document when the persistence write fails', async () => {
      const client = await joined('user-persist-2', 'doc-persist-error');
      harness.redis.set.mockRejectedValueOnce(new Error('redis down'));

      client.emit('document-update', {
        documentId: 'doc-persist-error',
        update: base64Update((doc) => doc.getText('content').insert(0, 'still here')),
      });
      await barrier(client, 'doc-persist-error');
      await drainAsyncWork();

      expect(
        harness.manager.getDocument('doc-persist-error')!.getText('content').toString(),
      ).toBe('still here');
      expect(client.connected).toBe(true);
    });

    it('does not persist an update that failed to apply', async () => {
      const client = await joined('user-persist-3', 'doc-persist-skip');
      harness.redis.set.mockClear();

      client.emit('document-update', { documentId: 'doc-persist-skip', update: '!!!' });
      await nextEvent(client, 'document-update-error');
      await drainAsyncWork();

      expect(harness.redis.set).not.toHaveBeenCalled();
    });

    it('flushes every open document on manager shutdown', async () => {
      // stop() only clears the periodic timers and flushes; the socket handlers
      // stay registered, so the harness remains usable afterwards.
      const client = await joined('user-persist-4', 'doc-shutdown-flush');
      client.emit('document-update', {
        documentId: 'doc-shutdown-flush',
        update: base64Update((doc) => doc.getText('content').insert(0, 'flush me')),
      });
      await barrier(client, 'doc-shutdown-flush');
      harness.redis.set.mockClear();

      await harness.manager.stop();

      expect(harness.redis.set).toHaveBeenCalledWith(
        'doc:state:doc-shutdown-flush',
        expect.any(Buffer),
        86400,
      );
    });
  });

  describe('snapshots', () => {
    it('creates a snapshot and notifies the requester and the room', async () => {
      const author = await joined('user-snap-1a', 'doc-snapshot-room');
      const peer = await joined('user-snap-1b', 'doc-snapshot-room');

      const mine = nextEvent<{ documentId: string; createdBy: string }>(
        author,
        'snapshot-created',
      );
      const theirs = nextEvent<{ documentId: string }>(peer, 'snapshot-created');
      author.emit('request-snapshot', {
        documentId: 'doc-snapshot-room',
        label: 'checkpoint',
      });

      expect(await mine).toMatchObject({
        documentId: 'doc-snapshot-room',
        createdBy: 'user-snap-1a',
      });
      expect((await theirs).documentId).toBe('doc-snapshot-room');
    });

    it('reports snapshot-error for a document that is not in memory', async () => {
      const client = await joined('user-snap-2', 'doc-snapshot-live');

      const error = nextEvent<{ documentId: string; error: string }>(
        client,
        'snapshot-error',
      );
      client.emit('request-snapshot', { documentId: 'doc-snapshot-missing' });

      expect(await error).toEqual({
        documentId: 'doc-snapshot-missing',
        error: 'Document not found',
      });
    });

    it('reports snapshot-error when the store rejects', async () => {
      const client = await joined('user-snap-3', 'doc-snapshot-fail');
      harness.redis.lpush.mockRejectedValueOnce(new Error('redis down'));

      const error = nextEvent<{ error: string }>(client, 'snapshot-error');
      client.emit('request-snapshot', { documentId: 'doc-snapshot-fail' });

      expect((await error).error).toBe('Failed to create snapshot');
    });

    it('keeps a snapshot list of maxSnapshots - 1 untrimmed (limit-1)', async () => {
      const client = await joined('user-snap-4', 'doc-trim-under');
      harness.redis.llen.mockResolvedValueOnce(MAX_SNAPSHOTS - 1);

      client.emit('request-snapshot', { documentId: 'doc-trim-under' });
      await nextEvent(client, 'snapshot-created');

      expect(harness.redis.ltrim).not.toHaveBeenCalled();
    });

    it('keeps a snapshot list of exactly maxSnapshots untrimmed (limit)', async () => {
      const client = await joined('user-snap-5', 'doc-trim-exact');
      harness.redis.llen.mockResolvedValueOnce(MAX_SNAPSHOTS);

      client.emit('request-snapshot', { documentId: 'doc-trim-exact' });
      await nextEvent(client, 'snapshot-created');

      expect(harness.redis.ltrim).not.toHaveBeenCalled();
    });

    it('trims once the list exceeds maxSnapshots (limit+1)', async () => {
      const client = await joined('user-snap-6', 'doc-trim-over');
      harness.redis.llen.mockResolvedValueOnce(MAX_SNAPSHOTS + 1);

      client.emit('request-snapshot', { documentId: 'doc-trim-over' });
      await nextEvent(client, 'snapshot-created');

      expect(harness.redis.ltrim).toHaveBeenCalledWith(
        'doc:snapshots:doc-trim-over',
        0,
        MAX_SNAPSHOTS - 1,
      );
    });

    it('labels an unlabelled snapshot as undefined rather than failing', async () => {
      const client = await joined('user-snap-7', 'doc-snapshot-nolabel');

      const created = nextEvent<{ label?: string }>(client, 'snapshot-created');
      client.emit('request-snapshot', { documentId: 'doc-snapshot-nolabel' });

      expect((await created).label).toBeUndefined();
    });
  });

  describe('history', () => {
    it('returns the stored snapshots for a document', async () => {
      const client = await joined('user-hist-1', 'doc-history');
      harness.redis.lrange.mockResolvedValueOnce([
        JSON.stringify({
          id: 'snap-1',
          documentId: 'doc-history',
          state: '',
          createdAt: '2024-01-01T00:00:00Z',
          createdBy: 'user-hist-1',
        }),
      ]);

      const history = nextEvent<{ documentId: string; snapshots: unknown[] }>(
        client,
        'document-history',
      );
      client.emit('request-history', { documentId: 'doc-history' });

      expect(await history).toMatchObject({
        documentId: 'doc-history',
        snapshots: [expect.objectContaining({ id: 'snap-1' })],
      });
    });

    it('defaults to a page of 20 when no limit is given', async () => {
      const client = await joined('user-hist-2', 'doc-history-default');

      client.emit('request-history', { documentId: 'doc-history-default' });
      await nextEvent(client, 'document-history');

      expect(harness.redis.lrange).toHaveBeenLastCalledWith(
        'doc:snapshots:doc-history-default',
        0,
        19,
      );
    });

    it('currently falls back to 20 for a limit of 0 (limit-1 of the smallest page)', async () => {
      // FINDING F8: `limit || 20` treats an explicit 0 as "unset", so a client
      // asking for no rows gets 20 instead of an empty page.
      const client = await joined('user-hist-3', 'doc-history-zero');

      client.emit('request-history', { documentId: 'doc-history-zero', limit: 0 });
      await nextEvent(client, 'document-history');

      expect(harness.redis.lrange).toHaveBeenLastCalledWith(
        'doc:snapshots:doc-history-zero',
        0,
        19,
      );
    });

    it('honours a limit of 1 (the smallest meaningful page)', async () => {
      const client = await joined('user-hist-4', 'doc-history-one');

      client.emit('request-history', { documentId: 'doc-history-one', limit: 1 });
      await nextEvent(client, 'document-history');

      expect(harness.redis.lrange).toHaveBeenLastCalledWith(
        'doc:snapshots:doc-history-one',
        0,
        0,
      );
    });

    it('currently forwards a limit above maxSnapshots unclamped (limit+1)', async () => {
      // FINDING F9: the requested page size is passed straight to Redis with no
      // server-side ceiling, so a client can ask for an unbounded range.
      const client = await joined('user-hist-5', 'doc-history-huge');

      client.emit('request-history', {
        documentId: 'doc-history-huge',
        limit: MAX_SNAPSHOTS + 1,
      });
      await nextEvent(client, 'document-history');

      expect(harness.redis.lrange).toHaveBeenLastCalledWith(
        'doc:snapshots:doc-history-huge',
        0,
        MAX_SNAPSHOTS,
      );
    });

    it('currently forwards a negative limit, producing an inverted Redis range', async () => {
      // FINDING F9 (same root cause): `limit: -1` becomes `lrange(key, 0, -2)`,
      // which in Redis means "everything except the last entry".
      const client = await joined('user-hist-6', 'doc-history-negative');

      client.emit('request-history', { documentId: 'doc-history-negative', limit: -1 });
      await nextEvent(client, 'document-history');

      expect(harness.redis.lrange).toHaveBeenLastCalledWith(
        'doc:snapshots:doc-history-negative',
        0,
        -2,
      );
    });

    it.skip('should clamp the history limit to [1, maxSnapshots] (FINDINGS F8, F9)', async () => {
      const client = await joined('user-hist-7', 'doc-history-clamped');

      client.emit('request-history', { documentId: 'doc-history-clamped', limit: -1 });
      await nextEvent(client, 'document-history');

      expect(harness.redis.lrange).toHaveBeenLastCalledWith(
        'doc:snapshots:doc-history-clamped',
        0,
        0,
      );
    });

    it('emits history-error when the store rejects', async () => {
      const client = await joined('user-hist-8', 'doc-history-error');
      harness.redis.lrange.mockRejectedValueOnce(new Error('redis down'));

      const error = nextEvent<{ documentId: string; error: string }>(
        client,
        'history-error',
      );
      client.emit('request-history', { documentId: 'doc-history-error' });

      expect(await error).toEqual({
        documentId: 'doc-history-error',
        error: 'Failed to retrieve history',
      });
    });

    it('returns an empty history for a document with no snapshots', async () => {
      const client = await joined('user-hist-9', 'doc-history-empty');

      const history = nextEvent<{ snapshots: unknown[] }>(client, 'document-history');
      client.emit('request-history', { documentId: 'doc-history-empty' });

      expect((await history).snapshots).toEqual([]);
    });

    it('serves history for a document the requester has not joined (no ownership check)', async () => {
      // FINDING F1: history is readable by any authenticated socket, mirroring
      // the missing access control on join-document.
      const client = await joined('user-hist-10', 'doc-history-home');

      const history = nextEvent<{ documentId: string }>(client, 'document-history');
      client.emit('request-history', { documentId: 'doc-history-someone-else' });

      expect((await history).documentId).toBe('doc-history-someone-else');
    });
  });
});
