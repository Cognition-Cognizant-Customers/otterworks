import { Socket as ClientSocket } from 'socket.io-client';
import * as Y from 'yjs';
import {
  Harness,
  barrier,
  connectUser,
  disconnectClients,
  joinDocument,
  startHarness,
  stopHarness,
} from './helpers/collab-harness';

/**
 * WP-11 — CRDT convergence through the live socket protocol.
 *
 * The point of a CRDT service is that update delivery order does not matter.
 * Every test here drives the same updates into two different documents on the
 * same server in opposite orders and asserts the resulting server-side states
 * are byte-for-byte equal, so nothing depends on timers or wall time.
 *
 * Determinism: no `setTimeout` waiting anywhere. Emits are sequenced with an
 * explicit round-trip barrier (`request-history` -> `document-history`); since
 * socket.io preserves per-connection ordering and the update handler applies
 * the CRDT update synchronously before yielding, receiving the barrier reply
 * proves every previously emitted update has been applied.
 */

/** A recorded Yjs update plus the state it produced, ready to replay. */
interface Edit {
  update: string;
}

function encodeUpdate(update: Uint8Array): Edit {
  return { update: Buffer.from(update).toString('base64') };
}

/** Produces an update that inserts `text` at `index` on top of `base`. */
function textEdit(base: Y.Doc, index: number, text: string): Edit {
  const fork = new Y.Doc();
  Y.applyUpdate(fork, Y.encodeStateAsUpdate(base));
  const before = Y.encodeStateVector(fork);
  fork.getText('content').insert(index, text);
  return encodeUpdate(Y.encodeStateAsUpdate(fork, before));
}

function mapEdit(base: Y.Doc, key: string, value: string): Edit {
  const fork = new Y.Doc();
  Y.applyUpdate(fork, Y.encodeStateAsUpdate(base));
  const before = Y.encodeStateVector(fork);
  fork.getMap('meta').set(key, value);
  return encodeUpdate(Y.encodeStateAsUpdate(fork, before));
}

function deleteEdit(base: Y.Doc, index: number, length: number): Edit {
  const fork = new Y.Doc();
  Y.applyUpdate(fork, Y.encodeStateAsUpdate(base));
  const before = Y.encodeStateVector(fork);
  fork.getText('content').delete(index, length);
  return encodeUpdate(Y.encodeStateAsUpdate(fork, before));
}

describe('CRDT convergence over the collaboration protocol (WP-11)', () => {
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

  async function join(client: ClientSocket, documentId: string): Promise<void> {
    const ack = await joinDocument(client, documentId);
    if (!ack.success) throw new Error(ack.error ?? 'join failed');
  }

  /**
   * Joins `documentId` with a dedicated client and emits `edits` in the given
   * order. A dedicated client per document matters: the manager evicts a
   * document as soon as its last participant leaves, and a client that joins a
   * second document leaves the first.
   */
  async function applyInOrder(
    userId: string,
    documentId: string,
    edits: Edit[],
  ): Promise<ClientSocket> {
    const client = await connect(userId);
    await join(client, documentId);
    for (const edit of edits) {
      client.emit('document-update', { documentId, update: edit.update });
    }
    await barrier(client, documentId);
    if (!harness.manager.getDocument(documentId)) {
      throw new Error(`document ${documentId} missing from server memory`);
    }
    return client;
  }

  function serverText(documentId: string): string {
    return harness.manager.getDocument(documentId)!.getText('content').toString();
  }

  function serverState(documentId: string): Uint8Array {
    return Y.encodeStateAsUpdate(harness.manager.getDocument(documentId)!);
  }

  describe('two clients', () => {
    it('converges on disjoint edits regardless of arrival order', async () => {
      const seed = new Y.Doc();
      const alice = textEdit(seed, 0, 'alpha');
      const bob = mapEdit(seed, 'title', 'Bravo');

      await applyInOrder('user-conv-1a', 'conv-disjoint-fwd', [alice, bob]);
      await applyInOrder('user-conv-1b', 'conv-disjoint-rev', [bob, alice]);

      expect(serverText('conv-disjoint-fwd')).toBe('alpha');
      expect(
        harness.manager.getDocument('conv-disjoint-fwd')!.getMap('meta').get('title'),
      ).toBe('Bravo');
      expect(serverText('conv-disjoint-rev')).toBe(serverText('conv-disjoint-fwd'));
      expect(
        Y.encodeStateVector(harness.manager.getDocument('conv-disjoint-rev')!),
      ).toEqual(Y.encodeStateVector(harness.manager.getDocument('conv-disjoint-fwd')!));
    });

    it('converges on overlapping inserts at the same index regardless of order', async () => {
      const seed = new Y.Doc();
      seed.getText('content').insert(0, 'shared');
      const seedEdit = encodeUpdate(Y.encodeStateAsUpdate(seed));
      const left = textEdit(seed, 3, 'LEFT');
      const right = textEdit(seed, 3, 'RIGHT');

      await applyInOrder('user-conv-2a', 'conv-overlap-fwd', [seedEdit, left, right]);
      await applyInOrder('user-conv-2b', 'conv-overlap-rev', [seedEdit, right, left]);

      const forward = serverText('conv-overlap-fwd');
      expect(forward).toBe(serverText('conv-overlap-rev'));
      expect(forward).toContain('LEFT');
      expect(forward).toContain('RIGHT');
      expect(forward).toHaveLength('shared'.length + 'LEFT'.length + 'RIGHT'.length);
    });

    it('converges when two clients concurrently delete overlapping ranges', async () => {
      const seed = new Y.Doc();
      seed.getText('content').insert(0, 'abcdefghij');
      const seedEdit = encodeUpdate(Y.encodeStateAsUpdate(seed));
      const cutFront = deleteEdit(seed, 0, 5);
      const cutMiddle = deleteEdit(seed, 3, 5);

      await applyInOrder('user-conv-3a', 'conv-del-fwd', [seedEdit, cutFront, cutMiddle]);
      await applyInOrder('user-conv-3b', 'conv-del-rev', [seedEdit, cutMiddle, cutFront]);

      expect(serverText('conv-del-fwd')).toBe('ij');
      expect(serverText('conv-del-rev')).toBe('ij');
    });

    it('converges when both clients set the same map key concurrently', async () => {
      const seed = new Y.Doc();
      const first = mapEdit(seed, 'status', 'draft');
      const second = mapEdit(seed, 'status', 'published');

      await applyInOrder('user-conv-4a', 'conv-map-fwd', [first, second]);
      await applyInOrder('user-conv-4b', 'conv-map-rev', [second, first]);

      const forward = harness.manager
        .getDocument('conv-map-fwd')!
        .getMap('meta')
        .get('status');
      const reverse = harness.manager
        .getDocument('conv-map-rev')!
        .getMap('meta')
        .get('status');
      expect(forward).toBe(reverse);
      expect(['draft', 'published']).toContain(forward);
    });

    it('broadcasts the exact update bytes so a peer reaches the same state as the server', async () => {
      const alice = await connect('user-conv-5a');
      const bob = await connect('user-conv-5b');
      await join(alice, 'conv-broadcast');
      await join(bob, 'conv-broadcast');

      const seed = new Y.Doc();
      const edit = textEdit(seed, 0, 'mirrored');
      const received = new Promise<{ update: string }>((resolve) => {
        bob.once('document-update', resolve);
      });

      alice.emit('document-update', {
        documentId: 'conv-broadcast',
        update: edit.update,
      });
      const payload = await received;
      await barrier(alice, 'conv-broadcast');

      const peer = new Y.Doc();
      Y.applyUpdate(peer, new Uint8Array(Buffer.from(payload.update, 'base64')));
      expect(peer.getText('content').toString()).toBe(serverText('conv-broadcast'));
    });
  });

  describe('three clients', () => {
    it('converges for three concurrent inserts under two different orders', async () => {
      const seed = new Y.Doc();
      seed.getText('content').insert(0, 'base');
      const seedEdit = encodeUpdate(Y.encodeStateAsUpdate(seed));
      const a = textEdit(seed, 0, 'A');
      const b = textEdit(seed, 2, 'B');
      const c = textEdit(seed, 4, 'C');

      await applyInOrder('user-conv-6a', 'conv-three-fwd', [seedEdit, a, b, c]);
      await applyInOrder('user-conv-6b', 'conv-three-rev', [seedEdit, c, b, a]);
      await applyInOrder('user-conv-6c', 'conv-three-mix', [seedEdit, b, a, c]);

      const forward = serverText('conv-three-fwd');
      expect(serverText('conv-three-rev')).toBe(forward);
      expect(serverText('conv-three-mix')).toBe(forward);
      expect(forward).toHaveLength(7);
    });

    it('lets three connected clients all observe each other\u2019s updates', async () => {
      const [a, b, c] = await Promise.all([
        connect('user-conv-7a'),
        connect('user-conv-7b'),
        connect('user-conv-7c'),
      ]);
      await join(a, 'conv-three-live');
      await join(b, 'conv-three-live');
      await join(c, 'conv-three-live');

      const seen = Promise.all([
        new Promise<{ update: string }>((r) => b.once('document-update', r)),
        new Promise<{ update: string }>((r) => c.once('document-update', r)),
      ]);

      const seed = new Y.Doc();
      const edit = textEdit(seed, 0, 'fanout');
      a.emit('document-update', { documentId: 'conv-three-live', update: edit.update });

      const [fromB, fromC] = await seen;
      expect(fromB.update).toBe(edit.update);
      expect(fromC.update).toBe(edit.update);
      await barrier(a, 'conv-three-live');
      expect(serverText('conv-three-live')).toBe('fanout');
    });
  });

  describe('duplicate and out-of-order delivery', () => {
    it('is idempotent when the same update is delivered three times', async () => {
      const seed = new Y.Doc();
      const edit = textEdit(seed, 0, 'once');

      await applyInOrder('user-dup-1a', 'conv-dup-single', [edit]);
      await applyInOrder('user-dup-1b', 'conv-dup-triple', [edit, edit, edit]);

      expect(serverText('conv-dup-triple')).toBe('once');
      expect(serverState('conv-dup-triple')).toEqual(serverState('conv-dup-single'));
    });

    it('buffers a causally-dependent update that arrives before its predecessor', async () => {
      const seed = new Y.Doc();
      const first = textEdit(seed, 0, 'first ');
      const staged = new Y.Doc();
      Y.applyUpdate(staged, new Uint8Array(Buffer.from(first.update, 'base64')));
      const second = textEdit(staged, 6, 'second');

      await applyInOrder('user-dup-2a', 'conv-order-causal', [first, second]);
      await applyInOrder('user-dup-2b', 'conv-order-reversed', [second, first]);

      expect(serverText('conv-order-causal')).toBe('first second');
      expect(serverText('conv-order-reversed')).toBe('first second');
    });

    it('holds a dependent update pending until its predecessor arrives', async () => {
      const seed = new Y.Doc();
      const first = textEdit(seed, 0, 'root ');
      const staged = new Y.Doc();
      Y.applyUpdate(staged, new Uint8Array(Buffer.from(first.update, 'base64')));
      const second = textEdit(staged, 5, 'leaf');

      const client = await applyInOrder('user-dup-3', 'conv-pending', [second]);
      expect(serverText('conv-pending')).toBe('');

      client.emit('document-update', {
        documentId: 'conv-pending',
        update: first.update,
      });
      await barrier(client, 'conv-pending');
      expect(serverText('conv-pending')).toBe('root leaf');
    });

    it('accepts a re-sent full state after incremental updates without duplicating content', async () => {
      const seed = new Y.Doc();
      seed.getText('content').insert(0, 'complete');
      const incremental = encodeUpdate(Y.encodeStateAsUpdate(seed));
      const fullState = encodeUpdate(Y.encodeStateAsUpdate(seed));

      await applyInOrder('user-dup-4', 'conv-full-resend', [incremental, fullState]);

      expect(serverText('conv-full-resend')).toBe('complete');
    });

    it('treats a no-op update (empty diff) as a no-op', async () => {
      const seed = new Y.Doc();
      seed.getText('content').insert(0, 'stable');
      const initial = encodeUpdate(Y.encodeStateAsUpdate(seed));
      const noop = encodeUpdate(Y.encodeStateAsUpdate(seed, Y.encodeStateVector(seed)));

      const client = await applyInOrder('user-dup-5', 'conv-noop', [initial]);
      const before = serverState('conv-noop');

      client.emit('document-update', { documentId: 'conv-noop', update: noop.update });
      await barrier(client, 'conv-noop');

      expect(serverText('conv-noop')).toBe('stable');
      expect(serverState('conv-noop')).toEqual(before);
    });
  });

  describe('joining and state sync', () => {
    it('syncs the merged state to a late joiner', async () => {
      const seed = new Y.Doc();
      const edit = textEdit(seed, 0, 'already here');
      await applyInOrder('user-sync-a', 'conv-late-join', [edit]);

      const late = await connect('user-sync-b');
      const sync = new Promise<{ documentId: string; state: string }>((resolve) => {
        late.once('sync-document', resolve);
      });
      late.emit('join-document', { documentId: 'conv-late-join' }, () => {});

      const payload = await sync;
      const reconstructed = new Y.Doc();
      Y.applyUpdate(reconstructed, new Uint8Array(Buffer.from(payload.state, 'base64')));
      expect(reconstructed.getText('content').toString()).toBe('already here');
    });

    it('syncs an empty state for a brand-new document', async () => {
      const client = await connect('user-sync-c');
      const sync = new Promise<{ state: string }>((resolve) => {
        client.once('sync-document', resolve);
      });
      client.emit('join-document', { documentId: 'conv-fresh-doc' }, () => {});

      const payload = await sync;
      const reconstructed = new Y.Doc();
      Y.applyUpdate(reconstructed, new Uint8Array(Buffer.from(payload.state, 'base64')));
      expect(reconstructed.getText('content').toString()).toBe('');
    });

    it('rebuilds a reconnecting client with a stale state vector via a server diff', async () => {
      const seed = new Y.Doc();
      const first = textEdit(seed, 0, 'v1 ');
      const client = await applyInOrder('user-sync-d', 'conv-stale-vector', [first]);

      // Snapshot the state the "disconnected" client last knew about.
      const staleReplica = new Y.Doc();
      Y.applyUpdate(staleReplica, serverState('conv-stale-vector'));
      const staleVector = Y.encodeStateVector(staleReplica);

      const staged = new Y.Doc();
      Y.applyUpdate(staged, serverState('conv-stale-vector'));
      const second = textEdit(staged, 3, 'v2');
      client.emit('document-update', {
        documentId: 'conv-stale-vector',
        update: second.update,
      });
      await barrier(client, 'conv-stale-vector');

      const diff = Y.encodeStateAsUpdate(
        harness.manager.getDocument('conv-stale-vector')!,
        staleVector,
      );
      Y.applyUpdate(staleReplica, diff);

      expect(staleReplica.getText('content').toString()).toBe(
        serverText('conv-stale-vector'),
      );
      expect(diff.byteLength).toBeLessThan(serverState('conv-stale-vector').byteLength);
    });

    it('restores a document from persisted state when it is joined again after eviction', async () => {
      const client = await connect('user-sync-e');
      const seed = new Y.Doc();
      seed.getText('content').insert(0, 'persisted');
      const persisted = Buffer.from(Y.encodeStateAsUpdate(seed));
      harness.redis.get.mockResolvedValueOnce(persisted);

      await join(client, 'conv-restored');

      expect(serverText('conv-restored')).toBe('persisted');
    });
  });

  describe('snapshot round-trip', () => {
    it('produces a snapshot whose state reconstructs an equal document', async () => {
      const seed = new Y.Doc();
      seed.getText('content').insert(0, 'snapshot me');
      seed.getMap('meta').set('kind', 'test');
      const edit = encodeUpdate(Y.encodeStateAsUpdate(seed));
      const client = await applyInOrder('user-snap-1', 'conv-snapshot', [edit]);

      const created = new Promise<{ state: string; label?: string }>((resolve) => {
        client.once('snapshot-created', resolve);
      });
      client.emit('request-snapshot', {
        documentId: 'conv-snapshot',
        label: 'round-trip',
      });
      const snapshot = await created;

      const restored = new Y.Doc();
      Y.applyUpdate(restored, new Uint8Array(Buffer.from(snapshot.state, 'base64')));
      expect(snapshot.label).toBe('round-trip');
      expect(restored.getText('content').toString()).toBe('snapshot me');
      expect(restored.getMap('meta').get('kind')).toBe('test');
      expect(Y.encodeStateVector(restored)).toEqual(
        Y.encodeStateVector(harness.manager.getDocument('conv-snapshot')!),
      );
    });

    it('restoring a snapshot on top of newer edits keeps the newer edits', async () => {
      const seed = new Y.Doc();
      const first = textEdit(seed, 0, 'old');
      const client = await applyInOrder('user-snap-2', 'conv-snapshot-merge', [first]);

      const created = new Promise<{ state: string }>((resolve) => {
        client.once('snapshot-created', resolve);
      });
      client.emit('request-snapshot', { documentId: 'conv-snapshot-merge' });
      const snapshot = await created;

      const staged = new Y.Doc();
      Y.applyUpdate(staged, serverState('conv-snapshot-merge'));
      const newer = textEdit(staged, 3, '+new');
      client.emit('document-update', {
        documentId: 'conv-snapshot-merge',
        update: newer.update,
      });
      client.emit('document-update', {
        documentId: 'conv-snapshot-merge',
        update: snapshot.state,
      });
      await barrier(client, 'conv-snapshot-merge');

      expect(serverText('conv-snapshot-merge')).toBe('old+new');
    });
  });
});
