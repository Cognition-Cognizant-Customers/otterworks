import type { Server as SocketIOServer } from 'socket.io';
import { PresenceHandler } from '../handlers/presence';
import { AwarenessService } from '../services/awareness';

const mockLogger = {
  info: jest.fn(),
  warn: jest.fn(),
  error: jest.fn(),
  debug: jest.fn(),
  fatal: jest.fn(),
  trace: jest.fn(),
  child: jest.fn().mockReturnThis(),
  level: 'info',
} as never;

interface MockSocket {
  emit: jest.Mock;
  leave: jest.Mock;
  to: jest.Mock;
}

function createMockIo(socketIds: string[] = []): {
  io: SocketIOServer;
  emit: jest.Mock;
  sockets: Map<string, MockSocket>;
} {
  const emit = jest.fn();
  const sockets = new Map<string, MockSocket>();
  for (const id of socketIds) {
    const socketEmit = jest.fn();
    sockets.set(id, {
      emit: socketEmit,
      leave: jest.fn(),
      to: jest.fn().mockReturnValue({ emit: jest.fn() }),
    });
  }
  const io = {
    to: jest.fn().mockReturnValue({ emit }),
    sockets: { sockets },
  } as unknown as SocketIOServer;
  return { io, emit, sockets };
}

describe('PresenceHandler', () => {
  let awareness: AwarenessService;
  let handler: PresenceHandler;

  beforeEach(() => {
    jest.clearAllMocks();
    awareness = new AwarenessService(mockLogger);
    handler = new PresenceHandler(awareness, mockLogger);
  });

  describe('getDocumentPresence', () => {
    it('returns empty presence for an unknown document', () => {
      const presence = handler.getDocumentPresence('missing-doc');

      expect(presence.documentId).toBe('missing-doc');
      expect(presence.users).toEqual([]);
      expect(presence.count).toBe(0);
    });

    it('returns all users of a document with the correct count', () => {
      awareness.addUser('doc-1', 's1', 'u1', 'Alice', 'a@t.com');
      awareness.addUser('doc-1', 's2', 'u2', 'Bob', 'b@t.com');

      const presence = handler.getDocumentPresence('doc-1');

      expect(presence.count).toBe(2);
      expect(presence.users.map((u) => u.userId).sort()).toEqual(['u1', 'u2']);
    });
  });

  describe('getActiveDocuments', () => {
    it('returns an empty list when no documents are active', () => {
      expect(handler.getActiveDocuments()).toEqual([]);
    });

    it('lists each active document with its user count', () => {
      awareness.addUser('doc-1', 's1', 'u1', 'Alice', 'a@t.com');
      awareness.addUser('doc-1', 's2', 'u2', 'Bob', 'b@t.com');
      awareness.addUser('doc-2', 's3', 'u3', 'Carol', 'c@t.com');

      const active = handler.getActiveDocuments();

      expect(active).toHaveLength(2);
      expect(active).toContainEqual({ documentId: 'doc-1', userCount: 2 });
      expect(active).toContainEqual({ documentId: 'doc-2', userCount: 1 });
    });
  });

  describe('broadcastPresenceUpdate', () => {
    it('emits presence-update to the document room', () => {
      awareness.addUser('doc-1', 's1', 'u1', 'Alice', 'a@t.com');
      const { io, emit } = createMockIo();

      handler.broadcastPresenceUpdate(io, 'doc-1');

      expect(io.to).toHaveBeenCalledWith('doc:doc-1');
      expect(emit).toHaveBeenCalledWith(
        'presence-update',
        expect.objectContaining({ documentId: 'doc-1', count: 1 }),
      );
    });
  });

  describe('startCleanupInterval', () => {
    beforeEach(() => {
      jest.useFakeTimers();
    });

    afterEach(() => {
      jest.useRealTimers();
    });

    it('removes stale users, notifies their sockets, and broadcasts presence', () => {
      jest.setSystemTime(0);
      awareness.addUser('doc-1', 's1', 'u1', 'Alice', 'a@t.com');
      const { io, emit, sockets } = createMockIo(['s1']);

      const timer = handler.startCleanupInterval(io, 1000, 500);
      jest.setSystemTime(2000);
      jest.advanceTimersByTime(1000);

      const evicted = sockets.get('s1');
      expect(evicted?.emit).toHaveBeenCalledWith('session-expired', {
        documentId: 'doc-1',
        reason: 'idle_timeout',
      });
      expect(evicted?.leave).toHaveBeenCalledWith('doc:doc-1');
      expect(emit).toHaveBeenCalledWith(
        'presence-update',
        expect.objectContaining({ documentId: 'doc-1', count: 0 }),
      );
      clearInterval(timer);
    });

    it('invokes onDocumentEmpty when the last user is evicted', () => {
      jest.setSystemTime(0);
      awareness.addUser('doc-1', 's1', 'u1', 'Alice', 'a@t.com');
      const { io } = createMockIo();
      const onDocumentEmpty = jest.fn();

      const timer = handler.startCleanupInterval(io, 1000, 500, onDocumentEmpty);
      jest.setSystemTime(2000);
      jest.advanceTimersByTime(1000);

      expect(onDocumentEmpty).toHaveBeenCalledWith('doc-1');
      clearInterval(timer);
    });

    it('does not evict active users', () => {
      jest.setSystemTime(0);
      awareness.addUser('doc-1', 's1', 'u1', 'Alice', 'a@t.com');
      const { io } = createMockIo(['s1']);
      const onDocumentEmpty = jest.fn();

      const timer = handler.startCleanupInterval(io, 1000, 60000, onDocumentEmpty);
      jest.advanceTimersByTime(1000);

      expect(onDocumentEmpty).not.toHaveBeenCalled();
      expect(handler.getDocumentPresence('doc-1').count).toBe(1);
      clearInterval(timer);
    });
  });
});
