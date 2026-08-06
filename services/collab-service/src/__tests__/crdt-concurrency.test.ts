import { createHarness, type Harness } from './helpers/collab-harness';
import type { FakeSocket } from './helpers/fake-socket';
import { createReplica, emptyUpdateB64, readDoc, type Replica } from './helpers/yjs';

/**
 * Concurrency and convergence behaviour of the CRDT pipeline.
 *
 * Every scenario is driven through CollaborationManager's real socket handlers with an
 * in-process socket stand-in, so update ordering is chosen by the test rather than by the
 * network. Replicas use fixed Yjs client ids, which makes concurrent-edit tie-breaking
 * deterministic run to run.
 */
describe('CRDT concurrency', () => {
  const DOC = 'doc-crdt';
  const ROOM = `doc:${DOC}`;

  let h: Harness;

  beforeEach(() => {
    h = createHarness();
  });

  afterEach(async () => {
    await h.stop();
  });

  /** The update the server relayed to the rest of the room for this socket's last send. */
  function relayedUpdate(socket: FakeSocket): string {
    const relayed = socket.broadcastsOf('document-update');
    const last = relayed[relayed.length - 1];
    if (!last) throw new Error(`socket ${socket.id} relayed no document-update`);
    expect(last.room).toBe(ROOM);
    return (last.payload as { update: string }).update;
  }

  async function send(socket: FakeSocket, update: string): Promise<void> {
    await socket.fire('document-update', { documentId: DOC, update });
  }

  async function twoClients(): Promise<{
    alice: FakeSocket;
    bob: FakeSocket;
    aliceDoc: Replica;
    bobDoc: Replica;
  }> {
    const { socket: alice } = await h.join('sock-a', 'user-a', DOC);
    const { socket: bob } = await h.join('sock-b', 'user-b', DOC);
    return {
      alice,
      bob,
      aliceDoc: createReplica({ state: h.lastSyncState(alice), clientId: 1001 }),
      bobDoc: createReplica({ state: h.lastSyncState(bob), clientId: 2002 }),
    };
  }

  function expectConverged(a: Replica, b: Replica): string {
    const serverDoc = h.manager.getDocument(DOC);
    expect(serverDoc).toBeDefined();
    expect(a.read()).toBe(b.read());
    expect(readDoc(serverDoc!)).toBe(a.read());
    return a.read();
  }

  describe('concurrent edits to the same region', () => {
    it('converges two clients that insert into the same empty document', async () => {
      const { alice, bob, aliceDoc, bobDoc } = await twoClients();

      aliceDoc.text().insert(0, 'alice');
      bobDoc.text().insert(0, 'bob');
      const fromAlice = aliceDoc.lastUpdate();
      const fromBob = bobDoc.lastUpdate();

      await send(alice, fromAlice);
      await send(bob, fromBob);

      bobDoc.applyB64(relayedUpdate(alice));
      aliceDoc.applyB64(relayedUpdate(bob));

      const converged = expectConverged(aliceDoc, bobDoc);
      expect(converged).toContain('alice');
      expect(converged).toContain('bob');
      expect(converged).toHaveLength('alicebob'.length);
    });

    it('converges concurrent inserts at the same index of a shared base', async () => {
      const { alice, bob, aliceDoc, bobDoc } = await twoClients();

      aliceDoc.text().insert(0, 'hello world');
      const base = aliceDoc.lastUpdate();
      await send(alice, base);
      bobDoc.applyB64(relayedUpdate(alice));
      expect(bobDoc.read()).toBe('hello world');

      // Both edit index 5 without having seen the other's edit.
      aliceDoc.text().insert(5, ' BIG');
      bobDoc.text().insert(5, ' tiny');
      const fromAlice = aliceDoc.lastUpdate();
      const fromBob = bobDoc.lastUpdate();

      await send(alice, fromAlice);
      await send(bob, fromBob);
      bobDoc.applyB64(relayedUpdate(alice));
      aliceDoc.applyB64(relayedUpdate(bob));

      const converged = expectConverged(aliceDoc, bobDoc);
      expect(converged).toContain(' BIG');
      expect(converged).toContain(' tiny');
      expect(converged.startsWith('hello')).toBe(true);
      expect(converged.endsWith(' world')).toBe(true);
    });

    it('converges a delete that overlaps a concurrent insert', async () => {
      const { alice, bob, aliceDoc, bobDoc } = await twoClients();

      aliceDoc.text().insert(0, 'abcdefgh');
      await send(alice, aliceDoc.lastUpdate());
      bobDoc.applyB64(relayedUpdate(alice));

      aliceDoc.text().delete(2, 4); // removes "cdef"
      bobDoc.text().insert(4, 'XY'); // inserts inside the range alice deleted
      const fromAlice = aliceDoc.lastUpdate();
      const fromBob = bobDoc.lastUpdate();

      await send(alice, fromAlice);
      await send(bob, fromBob);
      bobDoc.applyB64(relayedUpdate(alice));
      aliceDoc.applyB64(relayedUpdate(bob));

      const converged = expectConverged(aliceDoc, bobDoc);
      expect(converged).toBe('abXYgh');
    });

    it('converges three clients editing the same region concurrently', async () => {
      const { socket: a } = await h.join('sock-1', 'user-1', DOC);
      const { socket: b } = await h.join('sock-2', 'user-2', DOC);
      const { socket: c } = await h.join('sock-3', 'user-3', DOC);
      const docs = [
        createReplica({ state: h.lastSyncState(a), clientId: 11 }),
        createReplica({ state: h.lastSyncState(b), clientId: 22 }),
        createReplica({ state: h.lastSyncState(c), clientId: 33 }),
      ];

      docs[0].text().insert(0, 'one');
      docs[1].text().insert(0, 'two');
      docs[2].text().insert(0, 'three');
      const updates = docs.map((d) => d.lastUpdate());

      // Server sees them in one order; each replica sees the others in a different order.
      await send(a, updates[0]);
      await send(b, updates[1]);
      await send(c, updates[2]);
      docs[0].applyB64(updates[2]);
      docs[0].applyB64(updates[1]);
      docs[1].applyB64(updates[0]);
      docs[1].applyB64(updates[2]);
      docs[2].applyB64(updates[1]);
      docs[2].applyB64(updates[0]);

      expect(docs[1].read()).toBe(docs[0].read());
      expect(docs[2].read()).toBe(docs[0].read());
      expect(readDoc(h.manager.getDocument(DOC)!)).toBe(docs[0].read());
      expect(docs[0].read()).toHaveLength('onetwothree'.length);
    });

    it('keeps the server document authoritative when one client never applies the relay', async () => {
      const { alice, bob, aliceDoc, bobDoc } = await twoClients();

      aliceDoc.text().insert(0, 'server-side');
      await send(alice, aliceDoc.lastUpdate());
      bobDoc.text().insert(0, 'offline');
      await send(bob, bobDoc.lastUpdate());

      // Bob never applies alice's relay, so bob is behind — the server is not.
      const serverText = readDoc(h.manager.getDocument(DOC)!);
      expect(bobDoc.read()).toBe('offline');
      expect(serverText).toContain('server-side');
      expect(serverText).toContain('offline');
    });
  });

  describe('idempotency of repeated updates', () => {
    it('does not duplicate content when the same update is applied twice', async () => {
      const { alice, aliceDoc } = await twoClients();

      aliceDoc.text().insert(0, 'exactly once');
      const update = aliceDoc.lastUpdate();

      await send(alice, update);
      await send(alice, update);

      expect(readDoc(h.manager.getDocument(DOC)!)).toBe('exactly once');
    });

    it('leaves the server state byte-identical after a duplicate update', async () => {
      const { alice, aliceDoc } = await twoClients();

      aliceDoc.text().insert(0, 'stable');
      const update = aliceDoc.lastUpdate();
      await send(alice, update);
      const afterFirst = readDoc(h.manager.getDocument(DOC)!);

      await send(alice, update);

      expect(readDoc(h.manager.getDocument(DOC)!)).toBe(afterFirst);
    });

    it('does not duplicate content when a client replays its full state after reconnect', async () => {
      const { alice, aliceDoc } = await twoClients();

      aliceDoc.text().insert(0, 'reconnect me');
      await send(alice, aliceDoc.lastUpdate());

      // A reconnecting client typically pushes its whole state, not just a diff.
      await send(alice, aliceDoc.stateB64());

      expect(readDoc(h.manager.getDocument(DOC)!)).toBe('reconnect me');
    });

    it('relays a duplicate update to the room even though it is a state no-op', async () => {
      const { alice, aliceDoc } = await twoClients();

      aliceDoc.text().insert(0, 'dup');
      const update = aliceDoc.lastUpdate();
      await send(alice, update);
      await send(alice, update);

      // Deduplication is the client's job; the server relays both.
      expect(alice.broadcastsOf('document-update')).toHaveLength(2);
    });
  });

  describe('out-of-order and stale-clock updates', () => {
    it('converges when dependent updates arrive in reverse order', async () => {
      const { alice, aliceDoc } = await twoClients();

      aliceDoc.text().insert(0, 'first');
      const u1 = aliceDoc.lastUpdate();
      aliceDoc.text().insert(5, '-second');
      const u2 = aliceDoc.lastUpdate();

      await send(alice, u2); // depends on u1, which the server has not seen
      await send(alice, u1);

      expect(readDoc(h.manager.getDocument(DOC)!)).toBe('first-second');
    });

    it('withholds an orphaned update until its dependency arrives', async () => {
      const { alice, aliceDoc } = await twoClients();

      aliceDoc.text().insert(0, 'first');
      aliceDoc.lastUpdate(); // deliberately never sent
      aliceDoc.text().insert(5, '-second');
      const orphan = aliceDoc.lastUpdate();

      await send(alice, orphan);

      // Yjs parks the update; the text stays empty rather than showing a partial edit.
      expect(readDoc(h.manager.getDocument(DOC)!)).toBe('');
    });

    it('catches up a reconnecting client from its stale state vector', async () => {
      const { alice, bob, aliceDoc, bobDoc } = await twoClients();

      aliceDoc.text().insert(0, 'shared base');
      await send(alice, aliceDoc.lastUpdate());
      bobDoc.applyB64(relayedUpdate(alice));

      // Bob goes away; alice keeps editing.
      h.io.disconnect(bob, 'transport close');
      aliceDoc.text().insert(11, ' plus more');
      await send(alice, aliceDoc.lastUpdate());

      // Bob reconnects and syncs from the server's current state.
      const { socket: bobAgain } = await h.join('sock-b2', 'user-b', DOC);
      const bobReconnected = createReplica({
        state: h.lastSyncState(bobAgain),
        clientId: 2002,
      });

      expect(bobReconnected.read()).toBe('shared base plus more');
      expect(bobDoc.read()).toBe('shared base');
    });

    it('merges an offline edit made against a stale state vector', async () => {
      const { alice, bob, aliceDoc, bobDoc } = await twoClients();

      aliceDoc.text().insert(0, 'base');
      await send(alice, aliceDoc.lastUpdate());
      bobDoc.applyB64(relayedUpdate(alice));

      aliceDoc.text().insert(4, '-online');
      await send(alice, aliceDoc.lastUpdate());

      // Bob edited while disconnected, unaware of alice's "-online".
      bobDoc.text().insert(4, '-offline');
      await send(bob, bobDoc.lastUpdate());

      const serverText = readDoc(h.manager.getDocument(DOC)!);
      expect(serverText).toContain('-online');
      expect(serverText).toContain('-offline');
      expect(serverText.startsWith('base')).toBe(true);
    });

    it('sends only the missed delta when catching up a stale replica', async () => {
      const { alice, aliceDoc, bobDoc } = await twoClients();

      aliceDoc.text().insert(0, 'a'.repeat(200));
      await send(alice, aliceDoc.lastUpdate());
      bobDoc.applyB64(relayedUpdate(alice));

      aliceDoc.text().insert(200, 'bb');
      await send(alice, aliceDoc.lastUpdate());

      const catchUp = aliceDoc.diffSinceB64(bobDoc);
      bobDoc.applyB64(catchUp);

      expect(bobDoc.read()).toBe('a'.repeat(200) + 'bb');
      expect(Buffer.from(catchUp, 'base64').length).toBeLessThan(
        Buffer.from(aliceDoc.stateB64(), 'base64').length,
      );
    });
  });

  describe('update payload boundaries', () => {
    it('accepts the 2-byte update of an untouched document without changing state', async () => {
      const { alice, aliceDoc } = await twoClients();

      aliceDoc.text().insert(0, 'unchanged');
      await send(alice, aliceDoc.lastUpdate());

      await send(alice, emptyUpdateB64());

      expect(readDoc(h.manager.getDocument(DOC)!)).toBe('unchanged');
      expect(alice.emitsOf('document-update-error')).toHaveLength(0);
    });

    // FINDING (genuine): the handler applies an update of any size — the only size guard
    // is socket.io's default maxHttpBufferSize (1 MB), which drops the whole connection
    // without a `document-update-error`. See socket-transport.test.ts for that boundary.
    it('applies a large multi-megabyte update', async () => {
      const { alice, aliceDoc } = await twoClients();

      const chunk = 'x'.repeat(1024);
      for (let i = 0; i < 1024; i++) aliceDoc.text().insert(0, chunk);
      const bigUpdate = aliceDoc.stateB64();
      aliceDoc.drainUpdates();

      await send(alice, bigUpdate);

      expect(Buffer.from(bigUpdate, 'base64').length).toBeGreaterThan(1_000_000);
      expect(readDoc(h.manager.getDocument(DOC)!)).toHaveLength(1024 * 1024);
      expect(alice.emitsOf('document-update-error')).toHaveLength(0);
    });
  });
});
