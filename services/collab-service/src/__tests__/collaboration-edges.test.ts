import { createHarness, flushAsync, type Harness } from './helpers/collab-harness';
import type { FakeSocket } from './helpers/fake-socket';
import { createReplica, garbageUpdateB64, readDoc } from './helpers/yjs';

/**
 * Failure and abuse edges of the collaboration socket handlers: rejected payloads,
 * events from sockets that never joined, and client-controlled room targeting.
 *
 * Several cases pin behaviour that is arguably wrong (see the FINDING comments). They
 * assert today's behaviour on purpose so that changing it is a deliberate, visible act.
 */
describe('CollaborationManager edges', () => {
  const DOC = 'doc-edges';
  const ROOM = `doc:${DOC}`;

  let h: Harness;

  beforeEach(() => {
    h = createHarness({ maxSnapshots: 50 });
  });

  afterEach(async () => {
    await h.stop();
  });

  async function update(
    socket: FakeSocket,
    payload: { documentId?: string; update?: unknown },
  ): Promise<void> {
    await socket.fire('document-update', { documentId: DOC, ...payload });
  }

  function validUpdate(text: string): string {
    const replica = createReplica({ clientId: 7 });
    replica.text().insert(0, text);
    return replica.lastUpdate();
  }

  describe('joining a document', () => {
    it('acks success, registers presence and counts the room', async () => {
      const { socket, ack } = await h.join('sock-1', 'user-1', DOC);

      expect(ack).toEqual({ success: true });
      expect(socket.rooms.has(ROOM)).toBe(true);
      expect(h.awareness.getDocumentUserCount(DOC)).toBe(1);
      expect(await h.gaugeValue('activeRooms')).toBe(1);
      expect(h.manager.getDocumentCount()).toBe(1);
    });

    it('creates exactly one document when two sockets join simultaneously', async () => {
      const a = h.connect('sock-a', 'user-a');
      const b = h.connect('sock-b', 'user-b');

      await Promise.all([h.joinWith(a, DOC), h.joinWith(b, DOC)]);

      expect(h.manager.getDocumentCount()).toBe(1);
      expect(await h.gaugeValue('activeRooms')).toBe(1);
      expect(h.awareness.getDocumentUserCount(DOC)).toBe(2);
    });

    it('acks failure and leaves the room when loading persisted state fails', async () => {
      h.redis.failOn('get');
      const socket = h.connect('sock-fail', 'user-fail');

      const ack = await h.joinWith(socket, DOC);

      expect(ack).toEqual({ success: false, error: 'Failed to join document' });
      expect(socket.rooms.has(ROOM)).toBe(false);
      expect(h.awareness.getDocumentUserCount(DOC)).toBe(0);
      expect(await h.counterValue('connectionErrors', { reason: 'join_failed' })).toBe(1);
      expect(h.logger.messages('error')).toContain('join_document_failed');
    });

    it('accepts an empty documentId and opens a room for it', async () => {
      // FINDING (genuine, low severity): documentId is never validated, so "" is a room.
      const { socket, ack } = await h.join('sock-empty', 'user-1', '');

      expect(ack.success).toBe(true);
      expect(socket.rooms.has('doc:')).toBe(true);
      expect(h.awareness.getDocumentUserCount('')).toBe(1);
    });

    it('moves a socket out of its previous document when it joins another', async () => {
      const { socket } = await h.join('sock-move', 'user-1', DOC);
      socket.clearRecorded();
      h.io.clearRecorded();

      await h.joinWith(socket, 'doc-second');

      expect(socket.rooms.has(ROOM)).toBe(false);
      expect(socket.rooms.has('doc:doc-second')).toBe(true);
      expect(socket.broadcastsOf('user-left').map((b) => b.room)).toEqual([ROOM]);
      expect(h.awareness.getDocumentUserCount(DOC)).toBe(0);
      expect(h.awareness.getDocumentUserCount('doc-second')).toBe(1);
    });

    it('does not emit user-left when the same document is joined twice', async () => {
      const { socket } = await h.join('sock-rejoin', 'user-1', DOC);
      socket.clearRecorded();

      const ack = await h.joinWith(socket, DOC);

      expect(ack.success).toBe(true);
      expect(socket.broadcastsOf('user-left')).toHaveLength(0);
      expect(h.awareness.getDocumentUserCount(DOC)).toBe(1);
    });

    it('sends the joining client a sync-document containing the persisted text', async () => {
      const { socket: first } = await h.join('sock-1', 'user-1', DOC);
      await update(first, { update: validUpdate('persisted text') });

      const { socket: second } = await h.join('sock-2', 'user-2', DOC);

      const replica = createReplica({ state: h.lastSyncState(second) });
      expect(replica.read()).toBe('persisted text');
    });
  });

  describe('rejecting malformed and oversized updates', () => {
    it('emits document-update-error to the sender only for an undecodable update', async () => {
      const { socket: alice } = await h.join('sock-a', 'user-a', DOC);
      await h.join('sock-b', 'user-b', DOC);
      alice.clearRecorded();

      await update(alice, { update: garbageUpdateB64() });

      expect(alice.emitsOf('document-update-error')).toEqual([
        { documentId: DOC, error: 'Failed to apply update' },
      ]);
      expect(alice.broadcastsOf('document-update')).toHaveLength(0);
      expect(readDoc(h.manager.getDocument(DOC)!)).toBe('');
      expect(h.logger.messages('error')).toContain('crdt_apply_failed');
    });

    it('keeps the room usable after a rejected update', async () => {
      const { socket: alice } = await h.join('sock-a', 'user-a', DOC);
      const { socket: bob } = await h.join('sock-b', 'user-b', DOC);

      await update(alice, { update: garbageUpdateB64(4096) });
      await update(bob, { update: validUpdate('still working') });

      expect(readDoc(h.manager.getDocument(DOC)!)).toBe('still working');
      expect(bob.broadcastsOf('document-update')).toHaveLength(1);
      expect(h.awareness.getDocumentUserCount(DOC)).toBe(2);
    });

    it('rejects a 2 MB undecodable payload without evicting the room', async () => {
      const { socket } = await h.join('sock-a', 'user-a', DOC);

      await update(socket, { update: garbageUpdateB64(2 * 1024 * 1024) });

      expect(socket.emitsOf('document-update-error')).toHaveLength(1);
      expect(h.manager.getDocumentCount()).toBe(1);
      expect(h.awareness.getDocumentUserCount(DOC)).toBe(1);
    });

    it('rejects an empty update string', async () => {
      const { socket } = await h.join('sock-a', 'user-a', DOC);

      await update(socket, { update: '' });

      expect(socket.emitsOf('document-update-error')).toHaveLength(1);
      expect(socket.broadcastsOf('document-update')).toHaveLength(0);
    });

    it('relays a non-string update without applying it to the CRDT', async () => {
      // FINDING (genuine, low severity): only string payloads are decoded; any other
      // shape skips validation entirely and is broadcast verbatim to the room.
      const { socket } = await h.join('sock-a', 'user-a', DOC);

      await update(socket, { update: { ops: [{ insert: 'legacy patch' }] } });

      expect(socket.emitsOf('document-update-error')).toHaveLength(0);
      expect(socket.broadcastsOf('document-update')).toHaveLength(1);
      expect(readDoc(h.manager.getDocument(DOC)!)).toBe('');
    });

    it('ignores an update for a document that is not in memory', async () => {
      const socket = h.connect('sock-a', 'user-a');

      await update(socket, { documentId: 'never-opened', update: validUpdate('hi') });

      expect(socket.broadcastsOf('document-update')).toHaveLength(0);
      expect(socket.emitsOf('document-update-error')).toHaveLength(0);
      expect(h.logger.messages('warn')).toContain('document_update_for_unknown_doc');
    });

    it('still relays the update when persisting it to Redis fails', async () => {
      const { socket } = await h.join('sock-a', 'user-a', DOC);
      h.redis.failOn('set');

      await update(socket, { update: validUpdate('broadcast anyway') });

      expect(socket.broadcastsOf('document-update')).toHaveLength(1);
      expect(readDoc(h.manager.getDocument(DOC)!)).toBe('broadcast anyway');
      expect(h.logger.messages('error')).toContain('document_persist_failed');
    });
  });

  describe('updates from sockets that are not members of the room', () => {
    it('applies an update from a socket that never joined the document', async () => {
      // FINDING (genuine): handleDocumentUpdate never checks room membership, so any
      // authenticated socket can mutate any open document by naming its id.
      await h.join('sock-member', 'user-member', DOC);
      const outsider = h.connect('sock-outsider', 'user-outsider');

      await update(outsider, { update: validUpdate('written by an outsider') });

      expect(readDoc(h.manager.getDocument(DOC)!)).toBe('written by an outsider');
      expect(outsider.broadcastsOf('document-update')).toHaveLength(1);
    });

    it('applies an update sent by a socket that has already disconnected', async () => {
      // FINDING (genuine): the disconnect path clears presence but nothing rejects a
      // late/in-flight update from that socket while the room is still open.
      const { socket: alice } = await h.join('sock-a', 'user-a', DOC);
      await h.join('sock-b', 'user-b', DOC);

      h.io.disconnect(alice, 'transport close');
      alice.clearRecorded();
      await update(alice, { update: validUpdate('ghost edit') });

      expect(readDoc(h.manager.getDocument(DOC)!)).toBe('ghost edit');
      expect(alice.broadcastsOf('document-update')).toHaveLength(1);
      expect(h.awareness.getUserDocument(alice.id)).toBeNull();
    });

    it('ignores an update sent after the last client disconnected', async () => {
      const { socket } = await h.join('sock-a', 'user-a', DOC);

      h.io.disconnect(socket, 'transport close');
      await flushAsync();
      socket.clearRecorded();
      await update(socket, { update: validUpdate('too late') });

      expect(h.manager.getDocument(DOC)).toBeUndefined();
      expect(socket.broadcastsOf('document-update')).toHaveLength(0);
      expect(h.logger.messages('warn')).toContain('document_update_for_unknown_doc');
    });

    it('refreshes the sender lastActive so an active editor is not reaped', async () => {
      const { socket } = await h.join('sock-a', 'user-a', DOC);
      const [before] = h.awareness.getDocumentUsers(DOC);
      before.lastActive = 0;

      await update(socket, { update: validUpdate('typing') });

      const [after] = h.awareness.getDocumentUsers(DOC);
      expect(after.lastActive).toBeGreaterThan(0);
    });
  });

  describe('cursor and typing indicators', () => {
    it('broadcasts a cursor update with the identity from the token', async () => {
      const { socket } = await h.join('sock-a', 'user-a', DOC);

      socket.fire('cursor-update', {
        documentId: DOC,
        cursor: { index: 3, length: 0 },
        selection: null,
      });

      expect(socket.broadcastsOf('cursor-update')).toHaveLength(1);
      expect(socket.broadcastsOf('cursor-update')[0].payload).toMatchObject({
        userId: 'user-a',
        cursor: { index: 3, length: 0 },
      });
      expect(await h.counterValue('presenceUpdatesTotal')).toBe(1);
    });

    it('drops a cursor update from a socket that never joined', async () => {
      const socket = h.connect('sock-ghost', 'user-ghost');

      socket.fire('cursor-update', {
        documentId: DOC,
        cursor: { index: 0, length: 0 },
        selection: null,
      });

      expect(socket.broadcastsOf('cursor-update')).toHaveLength(0);
      expect(await h.counterValue('presenceUpdatesTotal')).toBe(0);
    });

    it('drops a cursor update sent after the socket disconnected', async () => {
      const { socket } = await h.join('sock-a', 'user-a', DOC);
      h.io.disconnect(socket, 'client namespace disconnect');
      socket.clearRecorded();

      socket.fire('cursor-update', {
        documentId: DOC,
        cursor: { index: 1, length: 0 },
        selection: null,
      });

      expect(socket.broadcastsOf('cursor-update')).toHaveLength(0);
    });

    it('stores cursor positions outside the document without validating them', async () => {
      // FINDING (genuine, low severity): cursor index/length are not range-checked.
      const { socket } = await h.join('sock-a', 'user-a', DOC);

      socket.fire('cursor-update', {
        documentId: DOC,
        cursor: { index: -1, length: Number.MAX_SAFE_INTEGER },
        selection: null,
      });

      const [user] = h.awareness.getDocumentUsers(DOC);
      expect(user.cursor).toEqual({ index: -1, length: Number.MAX_SAFE_INTEGER });
    });

    it('broadcasts a cursor update into a room the sender never joined', async () => {
      // FINDING (genuine): the target room comes from the client payload, not from the
      // document the awareness service has the socket in.
      const { socket } = await h.join('sock-a', 'user-a', DOC);
      socket.clearRecorded();

      socket.fire('cursor-update', {
        documentId: 'someone-elses-doc',
        cursor: { index: 0, length: 0 },
        selection: null,
      });

      expect(socket.broadcastsOf('cursor-update')[0].room).toBe('doc:someone-elses-doc');
    });

    it('toggles the typing indicator on and off', async () => {
      const { socket } = await h.join('sock-a', 'user-a', DOC);

      socket.fire('typing-indicator', { documentId: DOC, isTyping: true });
      expect(h.awareness.getDocumentUsers(DOC)[0].isTyping).toBe(true);

      socket.fire('typing-indicator', { documentId: DOC, isTyping: false });
      expect(h.awareness.getDocumentUsers(DOC)[0].isTyping).toBe(false);
      expect(socket.broadcastsOf('typing-indicator')).toHaveLength(2);
    });

    it('drops a typing indicator from a socket that never joined', async () => {
      const socket = h.connect('sock-ghost', 'user-ghost');

      socket.fire('typing-indicator', { documentId: DOC, isTyping: true });

      expect(socket.broadcastsOf('typing-indicator')).toHaveLength(0);
    });
  });

  describe('comment annotations', () => {
    const comment = {
      id: 'c-1',
      threadId: 't-1',
      content: 'looks good',
      rangeStart: 0,
      rangeEnd: 4,
    };

    it('echoes an added comment to the sender and the room', async () => {
      const { socket } = await h.join('sock-a', 'user-a', DOC);
      socket.clearRecorded();

      socket.fire('comment-add', { documentId: DOC, comment });

      expect(socket.emitsOf('comment-added')).toHaveLength(1);
      expect(socket.broadcastsOf('comment-added')).toHaveLength(1);
      expect(socket.broadcastsOf('comment-added')[0].room).toBe(ROOM);
    });

    it('overrides a client-supplied author with the authenticated identity', async () => {
      const { socket } = await h.join('sock-a', 'user-a', DOC);

      socket.fire('comment-add', {
        documentId: DOC,
        comment: {
          ...comment,
          author: { userId: 'user-victim', displayName: 'Victim' },
          createdAt: '1999-01-01T00:00:00.000Z',
        },
      });

      const [added] = socket.emitsOf('comment-added') as Array<{
        author: { userId: string };
        createdAt: string;
      }>;
      expect(added.author).toEqual({ userId: 'user-a', displayName: 'user-a' });
      expect(added.createdAt).not.toBe('1999-01-01T00:00:00.000Z');
    });

    it('overrides the documentId in the comment body with the event documentId', async () => {
      const { socket } = await h.join('sock-a', 'user-a', DOC);

      socket.fire('comment-add', {
        documentId: DOC,
        comment: { ...comment, documentId: 'doc-somewhere-else' },
      });

      const [added] = socket.emitsOf('comment-added') as Array<{ documentId: string }>;
      expect(added.documentId).toBe(DOC);
    });

    it('attributes a comment update to the authenticated identity', async () => {
      const { socket } = await h.join('sock-a', 'user-a', DOC);

      socket.fire('comment-update', {
        documentId: DOC,
        commentId: 'c-1',
        content: 'edited',
      });

      expect(socket.broadcastsOf('comment-updated')[0].payload).toMatchObject({
        commentId: 'c-1',
        content: 'edited',
        updatedBy: { userId: 'user-a' },
      });
    });

    it('attributes a comment deletion to the authenticated identity', async () => {
      const { socket } = await h.join('sock-a', 'user-a', DOC);

      socket.fire('comment-delete', { documentId: DOC, commentId: 'c-1' });

      expect(socket.broadcastsOf('comment-deleted')[0].payload).toEqual({
        commentId: 'c-1',
        deletedBy: 'user-a',
      });
    });

    it('lets a non-member socket post a comment into any room it names', async () => {
      // FINDING (genuine): comment events are relayed on the client-supplied room with
      // no membership or document-permission check.
      const outsider = h.connect('sock-outsider', 'user-outsider');

      outsider.fire('comment-add', { documentId: DOC, comment });

      expect(outsider.broadcastsOf('comment-added')[0].room).toBe(ROOM);
    });
  });

  describe('snapshots and history', () => {
    it('creates a snapshot and notifies the sender and the room', async () => {
      const { socket } = await h.join('sock-a', 'user-a', DOC);
      await update(socket, { update: validUpdate('snapshot me') });
      socket.clearRecorded();

      await socket.fire('request-snapshot', { documentId: DOC, label: 'v1' });

      const [created] = socket.emitsOf('snapshot-created') as Array<{
        label: string;
        createdBy: string;
      }>;
      expect(created).toMatchObject({ label: 'v1', createdBy: 'user-a' });
      expect(socket.broadcastsOf('snapshot-created')).toHaveLength(1);
    });

    it('returns snapshot-error for a document that is not open', async () => {
      const socket = h.connect('sock-a', 'user-a');

      await socket.fire('request-snapshot', { documentId: 'not-open' });

      expect(socket.emitsOf('snapshot-error')).toEqual([
        { documentId: 'not-open', error: 'Document not found' },
      ]);
    });

    it('returns snapshot-error when the store rejects', async () => {
      const { socket } = await h.join('sock-a', 'user-a', DOC);
      h.redis.failOn('lpush');
      socket.clearRecorded();

      await socket.fire('request-snapshot', { documentId: DOC });

      expect(socket.emitsOf('snapshot-error')).toHaveLength(1);
      expect(socket.broadcastsOf('snapshot-created')).toHaveLength(0);
      expect(h.logger.messages('error')).toContain('create_snapshot_failed');
    });

    it('returns history-error when the store rejects', async () => {
      const { socket } = await h.join('sock-a', 'user-a', DOC);
      h.redis.failOn('lrange');

      await socket.fire('request-history', { documentId: DOC });

      expect(socket.emitsOf('history-error')).toEqual([
        { documentId: DOC, error: 'Failed to retrieve history' },
      ]);
    });

    it('returns an empty history for a document with no snapshots', async () => {
      const { socket } = await h.join('sock-a', 'user-a', DOC);

      await socket.fire('request-history', { documentId: DOC });

      expect(socket.emitsOf('document-history')).toEqual([
        { documentId: DOC, snapshots: [] },
      ]);
    });

    it.each([
      ['limit-1', 49, 48],
      ['limit', 50, 49],
      ['limit+1', 51, 50],
    ])(
      'passes a history limit of %s (%i) straight through to Redis as 0..%i',
      async (_label, limit, expectedStop) => {
        const { socket } = await h.join('sock-a', 'user-a', DOC);
        const lrange = jest.spyOn(h.redis, 'lrange');

        await socket.fire('request-history', { documentId: DOC, limit });

        // FINDING (genuine, low severity): the requested limit is unbounded — it is not
        // clamped to maxSnapshots (50), so a client can ask for an arbitrary range.
        expect(lrange).toHaveBeenCalledWith(`doc:snapshots:${DOC}`, 0, expectedStop);
        lrange.mockRestore();
      },
    );

    it('defaults an omitted history limit to 20', async () => {
      const { socket } = await h.join('sock-a', 'user-a', DOC);
      const lrange = jest.spyOn(h.redis, 'lrange');

      await socket.fire('request-history', { documentId: DOC });

      expect(lrange).toHaveBeenCalledWith(`doc:snapshots:${DOC}`, 0, 19);
      lrange.mockRestore();
    });

    it('treats a history limit of 0 as the default 20 rather than an empty page', async () => {
      // FINDING (genuine, low severity): `limit || 20` cannot express "no rows".
      const { socket } = await h.join('sock-a', 'user-a', DOC);
      const lrange = jest.spyOn(h.redis, 'lrange');

      await socket.fire('request-history', { documentId: DOC, limit: 0 });

      expect(lrange).toHaveBeenCalledWith(`doc:snapshots:${DOC}`, 0, 19);
      lrange.mockRestore();
    });

    it('trims stored snapshots to maxSnapshots once the list grows past it', async () => {
      const small = createHarness({ maxSnapshots: 2 });
      try {
        const { socket } = await small.join('sock-s', 'user-s', DOC);
        for (let i = 0; i < 3; i++) {
          await socket.fire('request-snapshot', { documentId: DOC, label: `v${i}` });
        }

        expect(small.redis.lists.get(`doc:snapshots:${DOC}`)).toHaveLength(2);
        expect(socket.emitsOf('snapshot-created')).toHaveLength(3);
        expect(socket.emitsOf('snapshot-error')).toHaveLength(0);
      } finally {
        await small.stop();
      }
    });
  });
});
