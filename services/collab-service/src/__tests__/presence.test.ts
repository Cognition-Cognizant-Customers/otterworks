import type { Server as SocketIOServer } from 'socket.io';
import { PresenceHandler } from '../handlers/presence';
import { AwarenessService } from '../services/awareness';

interface RoomEmit {
  room: string;
  event: string;
  payload: unknown;
}

interface FakeSocket {
  id: string;
  emitted: Array<{ event: string; payload: unknown }>;
  left: string[];
  roomEmits: RoomEmit[];
  emit: (event: string, payload: unknown) => void;
  leave: (room: string) => void;
  to: (room: string) => { emit: (event: string, payload: unknown) => void };
}

function createFakeSocket(id: string): FakeSocket {
  const socket: FakeSocket = {
    id,
    emitted: [],
    left: [],
    roomEmits: [],
    emit: (event, payload) => {
      socket.emitted.push({ event, payload });
    },
    leave: (room) => {
      socket.left.push(room);
    },
    to: (room) => ({
      emit: (event, payload) => socket.roomEmits.push({ room, event, payload }),
    }),
  };
  return socket;
}

function createFakeIo() {
  const emits: RoomEmit[] = [];
  const sockets = new Map<string, FakeSocket>();

  const io = {
    to: (room: string) => ({
      emit: (event: string, payload: unknown) => emits.push({ room, event, payload }),
    }),
    sockets: { sockets },
  };

  return {
    io: io as unknown as SocketIOServer,
    emits,
    register: (socket: FakeSocket) => sockets.set(socket.id, socket),
  };
}

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

describe('PresenceHandler', () => {
  let awareness: AwarenessService;
  let presence: PresenceHandler;

  beforeEach(() => {
    jest.clearAllMocks();
    awareness = new AwarenessService(mockLogger);
    presence = new PresenceHandler(awareness, mockLogger);
  });

  describe('document presence', () => {
    it('test_getDocumentPresence_userJoined_reportsThatUser', () => {
      awareness.addUser('doc-1', 'socket-1', 'user-1', 'Alice', 'alice@test.com');

      const info = presence.getDocumentPresence('doc-1');

      expect(info.documentId).toBe('doc-1');
      expect(info.count).toBe(1);
      expect(info.users.map((u) => u.userId)).toEqual(['user-1']);
    });

    it('test_getDocumentPresence_noUsersJoined_reportsAnEmptyRoom', () => {
      const info = presence.getDocumentPresence('doc-nobody');

      expect(info.count).toBe(0);
      expect(info.users).toEqual([]);
    });

    it('test_getDocumentPresence_sameSocketJoinsTwice_countsTheUserOnce', () => {
      awareness.addUser('doc-1', 'socket-1', 'user-1', 'Alice', 'alice@test.com');
      awareness.addUser('doc-1', 'socket-1', 'user-1', 'Alice', 'alice@test.com');

      const info = presence.getDocumentPresence('doc-1');

      expect(info.count).toBe(1);
    });

    it('test_getDocumentPresence_sameUserOnTwoSockets_countsEachSession', () => {
      awareness.addUser('doc-1', 'socket-1', 'user-1', 'Alice', 'alice@test.com');
      awareness.addUser('doc-1', 'socket-2', 'user-1', 'Alice', 'alice@test.com');

      expect(presence.getDocumentPresence('doc-1').count).toBe(2);
    });

    it('test_getDocumentPresence_socketJoinsSecondDocument_movesTheUserOver', () => {
      awareness.addUser('doc-1', 'socket-1', 'user-1', 'Alice', 'alice@test.com');
      awareness.addUser('doc-2', 'socket-1', 'user-1', 'Alice', 'alice@test.com');

      expect(presence.getDocumentPresence('doc-1').count).toBe(0);
      expect(presence.getDocumentPresence('doc-2').count).toBe(1);
    });

    it('test_getDocumentPresence_oneOfTwoUsersLeaves_reportsTheRemainingUser', () => {
      awareness.addUser('doc-1', 'socket-1', 'user-1', 'Alice', 'alice@test.com');
      awareness.addUser('doc-1', 'socket-2', 'user-2', 'Bob', 'bob@test.com');

      awareness.removeUser('socket-1');

      const info = presence.getDocumentPresence('doc-1');
      expect(info.count).toBe(1);
      expect(info.users.map((u) => u.userId)).toEqual(['user-2']);
    });

    it('test_getDocumentPresence_socketLeavesWithoutEverJoining_leavesPresenceUnchanged', () => {
      awareness.addUser('doc-1', 'socket-1', 'user-1', 'Alice', 'alice@test.com');

      const removed = awareness.removeUser('socket-never-joined');

      expect(removed).toBeNull();
      expect(presence.getDocumentPresence('doc-1').count).toBe(1);
    });
  });

  describe('active documents', () => {
    it('test_getActiveDocuments_usersInSeveralDocuments_reportsEachWithItsUserCount', () => {
      awareness.addUser('doc-1', 'socket-1', 'user-1', 'Alice', 'alice@test.com');
      awareness.addUser('doc-1', 'socket-2', 'user-2', 'Bob', 'bob@test.com');
      awareness.addUser('doc-2', 'socket-3', 'user-3', 'Carol', 'carol@test.com');

      const active = presence
        .getActiveDocuments()
        .sort((a, b) => a.documentId.localeCompare(b.documentId));

      expect(active).toEqual([
        { documentId: 'doc-1', userCount: 2 },
        { documentId: 'doc-2', userCount: 1 },
      ]);
    });

    it('test_getActiveDocuments_lastUserLeavesTheRoom_dropsTheDocument', () => {
      awareness.addUser('doc-1', 'socket-1', 'user-1', 'Alice', 'alice@test.com');
      awareness.addUser('doc-2', 'socket-2', 'user-2', 'Bob', 'bob@test.com');

      awareness.removeUser('socket-1');

      expect(presence.getActiveDocuments()).toEqual([
        { documentId: 'doc-2', userCount: 1 },
      ]);
    });

    it('test_getActiveDocuments_noUsersAnywhere_reportsNoDocuments', () => {
      expect(presence.getActiveDocuments()).toEqual([]);
    });
  });

  describe('broadcastPresenceUpdate', () => {
    it('test_broadcastPresenceUpdate_always_sendsPresenceToTheDocumentRoom', () => {
      const { io, emits } = createFakeIo();
      awareness.addUser('doc-1', 'socket-1', 'user-1', 'Alice', 'alice@test.com');

      presence.broadcastPresenceUpdate(io, 'doc-1');

      expect(emits).toHaveLength(1);
      expect(emits[0].room).toBe('doc:doc-1');
      expect(emits[0].event).toBe('presence-update');
      expect(emits[0].payload).toMatchObject({ documentId: 'doc-1', count: 1 });
    });

    it('test_broadcastPresenceUpdate_emptyDocument_sendsAZeroCount', () => {
      const { io, emits } = createFakeIo();

      presence.broadcastPresenceUpdate(io, 'doc-empty');

      expect(emits[0].payload).toMatchObject({ documentId: 'doc-empty', count: 0 });
    });
  });

  describe('startCleanupInterval', () => {
    beforeEach(() => {
      jest.useFakeTimers();
    });

    afterEach(() => {
      jest.clearAllTimers();
      jest.useRealTimers();
    });

    it('test_startCleanupInterval_userIdleBeyondMaxIdle_evictsAndNotifiesThatSocket', () => {
      const { io, emits, register } = createFakeIo();
      const idle = createFakeSocket('socket-idle');
      register(idle);
      awareness.addUser('doc-1', 'socket-idle', 'user-1', 'Alice', 'alice@test.com');

      const timer = presence.startCleanupInterval(io, 60000, 300000);
      jest.advanceTimersByTime(360000);
      clearInterval(timer);

      expect(awareness.getDocumentUserCount('doc-1')).toBe(0);
      expect(idle.emitted).toEqual([
        {
          event: 'session-expired',
          payload: { documentId: 'doc-1', reason: 'idle_timeout' },
        },
      ]);
      expect(idle.left).toEqual(['doc:doc-1']);
      expect(idle.roomEmits).toEqual([
        {
          room: 'doc:doc-1',
          event: 'user-left',
          payload: { socketId: 'socket-idle', userId: 'user-1' },
        },
      ]);
      expect(emits.map((e) => e.event)).toEqual(['presence-update']);
    });

    it('test_startCleanupInterval_userStillWithinMaxIdle_keepsThatUser', () => {
      const { io, emits, register } = createFakeIo();
      const active = createFakeSocket('socket-active');
      register(active);
      awareness.addUser('doc-1', 'socket-active', 'user-1', 'Alice', 'alice@test.com');

      const timer = presence.startCleanupInterval(io, 60000, 300000);
      jest.advanceTimersByTime(240000);
      clearInterval(timer);

      expect(awareness.getDocumentUserCount('doc-1')).toBe(1);
      expect(active.emitted).toEqual([]);
      expect(emits).toEqual([]);
    });

    it('test_startCleanupInterval_lastUserEvicted_reportsTheDocumentAsEmpty', () => {
      const { io, register } = createFakeIo();
      register(createFakeSocket('socket-idle'));
      awareness.addUser('doc-1', 'socket-idle', 'user-1', 'Alice', 'alice@test.com');
      const emptied: string[] = [];

      const timer = presence.startCleanupInterval(io, 60000, 300000, (documentId) =>
        emptied.push(documentId),
      );
      jest.advanceTimersByTime(360000);
      clearInterval(timer);

      expect(emptied).toEqual(['doc-1']);
    });

    it('test_startCleanupInterval_activeUserRemainsInRoom_doesNotReportTheDocumentAsEmpty', () => {
      const { io, register } = createFakeIo();
      const idle = createFakeSocket('socket-idle');
      const active = createFakeSocket('socket-active');
      register(idle);
      register(active);
      awareness.addUser('doc-1', 'socket-idle', 'user-1', 'Alice', 'alice@test.com');
      awareness.addUser('doc-1', 'socket-active', 'user-2', 'Bob', 'bob@test.com');
      const emptied: string[] = [];

      const timer = presence.startCleanupInterval(io, 60000, 300000, (documentId) =>
        emptied.push(documentId),
      );
      jest.advanceTimersByTime(240000);
      awareness.refreshActivity('socket-active');
      jest.advanceTimersByTime(120000);
      clearInterval(timer);

      expect(awareness.getDocumentUserCount('doc-1')).toBe(1);
      expect(emptied).toEqual([]);
    });

    it('test_startCleanupInterval_evictedSocketAlreadyGone_stillUpdatesPresence', () => {
      const { io, emits } = createFakeIo();
      awareness.addUser('doc-1', 'socket-gone', 'user-1', 'Alice', 'alice@test.com');

      const timer = presence.startCleanupInterval(io, 60000, 300000);
      jest.advanceTimersByTime(360000);
      clearInterval(timer);

      expect(awareness.getDocumentUserCount('doc-1')).toBe(0);
      expect(emits.map((e) => e.event)).toEqual(['presence-update']);
    });
  });
});
