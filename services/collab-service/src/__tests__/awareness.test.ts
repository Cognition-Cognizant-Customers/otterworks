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

describe('AwarenessService', () => {
  let awareness: AwarenessService;

  beforeEach(() => {
    jest.clearAllMocks();
    awareness = new AwarenessService(mockLogger);
  });

  describe('addUser', () => {
    it('should add a user to a document', () => {
      const result = awareness.addUser(
        'doc-1',
        'socket-1',
        'user-1',
        'Alice',
        'alice@test.com',
      );

      expect(result.userId).toBe('user-1');
      expect(result.displayName).toBe('Alice');
      expect(result.email).toBe('alice@test.com');
      expect(result.color).toBeDefined();
      expect(result.cursor).toBeNull();
      expect(result.selection).toBeNull();
      expect(result.isTyping).toBe(false);
      expect(result.lastActive).toBeDefined();
    });

    it('should assign different colors to different users', () => {
      const user1 = awareness.addUser('doc-1', 's1', 'u1', 'Alice', 'a@t.com');
      const user2 = awareness.addUser('doc-1', 's2', 'u2', 'Bob', 'b@t.com');

      expect(user1.color).not.toBe(user2.color);
    });

    it('should track user count per document', () => {
      awareness.addUser('doc-1', 's1', 'u1', 'Alice', 'a@t.com');
      awareness.addUser('doc-1', 's2', 'u2', 'Bob', 'b@t.com');
      awareness.addUser('doc-2', 's3', 'u3', 'Charlie', 'c@t.com');

      expect(awareness.getDocumentUserCount('doc-1')).toBe(2);
      expect(awareness.getDocumentUserCount('doc-2')).toBe(1);
      expect(awareness.getDocumentUserCount('doc-3')).toBe(0);
    });
  });

  describe('removeUser', () => {
    it('should remove a user and return mapping', () => {
      awareness.addUser('doc-1', 'socket-1', 'user-1', 'Alice', 'a@t.com');

      const result = awareness.removeUser('socket-1');

      expect(result).toEqual({ documentId: 'doc-1', userId: 'user-1' });
      expect(awareness.getDocumentUserCount('doc-1')).toBe(0);
    });

    it('should return null for unknown socket', () => {
      const result = awareness.removeUser('unknown-socket');
      expect(result).toBeNull();
    });

    it('should clean up empty document states', () => {
      awareness.addUser('doc-1', 's1', 'u1', 'Alice', 'a@t.com');
      awareness.removeUser('s1');

      expect(awareness.getActiveDocumentIds()).not.toContain('doc-1');
    });

    it('should not remove document state when other users remain', () => {
      awareness.addUser('doc-1', 's1', 'u1', 'Alice', 'a@t.com');
      awareness.addUser('doc-1', 's2', 'u2', 'Bob', 'b@t.com');

      awareness.removeUser('s1');

      expect(awareness.getDocumentUserCount('doc-1')).toBe(1);
      expect(awareness.getActiveDocumentIds()).toContain('doc-1');
    });
  });

  describe('updateCursor', () => {
    it('should update cursor position', () => {
      awareness.addUser('doc-1', 's1', 'u1', 'Alice', 'a@t.com');

      const cursor = { index: 10, length: 0 };
      const selection = { index: 10, length: 5 };
      const result = awareness.updateCursor('s1', cursor, selection);

      expect(result).not.toBeNull();
      expect(result!.cursor).toEqual(cursor);
      expect(result!.selection).toEqual(selection);
    });

    it('should return null for unknown socket', () => {
      const result = awareness.updateCursor('unknown', { index: 0, length: 0 }, null);
      expect(result).toBeNull();
    });

    it('should update lastActive timestamp', () => {
      awareness.addUser('doc-1', 's1', 'u1', 'Alice', 'a@t.com');

      const before = Date.now();
      awareness.updateCursor('s1', { index: 5, length: 0 }, null);
      const users = awareness.getDocumentUsers('doc-1');

      expect(users[0].lastActive).toBeGreaterThanOrEqual(before);
    });
  });

  describe('setTyping', () => {
    it('should update typing state', () => {
      awareness.addUser('doc-1', 's1', 'u1', 'Alice', 'a@t.com');

      const result = awareness.setTyping('s1', true);
      expect(result).not.toBeNull();
      expect(result!.isTyping).toBe(true);

      const result2 = awareness.setTyping('s1', false);
      expect(result2!.isTyping).toBe(false);
    });

    it('should return null for unknown socket', () => {
      const result = awareness.setTyping('unknown', true);
      expect(result).toBeNull();
    });
  });

  describe('getDocumentUsers', () => {
    it('should return all users in a document', () => {
      awareness.addUser('doc-1', 's1', 'u1', 'Alice', 'a@t.com');
      awareness.addUser('doc-1', 's2', 'u2', 'Bob', 'b@t.com');

      const users = awareness.getDocumentUsers('doc-1');

      expect(users).toHaveLength(2);
      expect(users.map((u) => u.userId).sort((a, b) => a.localeCompare(b))).toEqual([
        'u1',
        'u2',
      ]);
    });

    it('should return empty array for unknown document', () => {
      const users = awareness.getDocumentUsers('doc-unknown');
      expect(users).toEqual([]);
    });
  });

  describe('getUserDocument', () => {
    it('should return the document a user is in', () => {
      awareness.addUser('doc-1', 's1', 'u1', 'Alice', 'a@t.com');

      expect(awareness.getUserDocument('s1')).toBe('doc-1');
    });

    it('should return null for unknown socket', () => {
      expect(awareness.getUserDocument('unknown')).toBeNull();
    });
  });

  describe('getActiveDocumentIds', () => {
    it('should return all active document IDs', () => {
      awareness.addUser('doc-1', 's1', 'u1', 'Alice', 'a@t.com');
      awareness.addUser('doc-2', 's2', 'u2', 'Bob', 'b@t.com');
      awareness.addUser('doc-3', 's3', 'u3', 'Charlie', 'c@t.com');

      const ids = awareness.getActiveDocumentIds().sort((a, b) => a.localeCompare(b));
      expect(ids).toEqual(['doc-1', 'doc-2', 'doc-3']);
    });
  });

  describe('cleanupStaleUsers', () => {
    it('should remove users idle beyond threshold', () => {
      awareness.addUser('doc-1', 's1', 'u1', 'Alice', 'a@t.com');

      // Manually set lastActive to the past
      const users = awareness.getDocumentUsers('doc-1');
      users[0].lastActive = Date.now() - 400000;

      const removed = awareness.cleanupStaleUsers(300000);

      expect(removed).toHaveLength(1);
      expect(removed[0].userId).toBe('u1');
      expect(awareness.getDocumentUserCount('doc-1')).toBe(0);
    });

    it('should not remove active users', () => {
      awareness.addUser('doc-1', 's1', 'u1', 'Alice', 'a@t.com');

      const removed = awareness.cleanupStaleUsers(300000);

      expect(removed).toHaveLength(0);
      expect(awareness.getDocumentUserCount('doc-1')).toBe(1);
    });
  });
});
