import { AwarenessService } from '../services/awareness';
import { createTestLogger, type TestLogger } from './helpers/test-logger';

const MAX_IDLE_MS = 300_000;
/** USER_COLORS in src/services/awareness.ts */
const PALETTE_SIZE = 20;
const EPOCH = Date.UTC(2026, 0, 1, 12, 0, 0);

/**
 * Boundary and eviction behaviour of AwarenessService.
 *
 * The clock is frozen with fake timers, so idle thresholds are asserted exactly instead
 * of being approximated with a real elapsed-time wait.
 */
describe('AwarenessService edges', () => {
  let awareness: AwarenessService;
  let logger: TestLogger;

  beforeEach(() => {
    jest.useFakeTimers({ doNotFake: ['setImmediate'] });
    jest.setSystemTime(EPOCH);
    logger = createTestLogger();
    awareness = new AwarenessService(logger.asLogger());
  });

  afterEach(() => {
    jest.useRealTimers();
  });

  describe('idle eviction boundary', () => {
    it.each([
      ['limit-1', MAX_IDLE_MS - 1, false],
      ['limit', MAX_IDLE_MS, false],
      ['limit+1', MAX_IDLE_MS + 1, true],
    ])('idle for %s (%ims) — evicted: %s', (_label, idleMs, shouldEvict) => {
      awareness.addUser('doc-1', 's1', 'u1', 'Alice', 'alice@test');
      jest.setSystemTime(EPOCH + idleMs);

      const removed = awareness.cleanupStaleUsers(MAX_IDLE_MS);

      expect(removed).toHaveLength(shouldEvict ? 1 : 0);
      expect(awareness.getDocumentUserCount('doc-1')).toBe(shouldEvict ? 0 : 1);
    });

    it('keeps a user that has just acted even with a zero idle allowance', () => {
      awareness.addUser('doc-1', 's1', 'u1', 'Alice', 'alice@test');

      // `now - lastActive > maxIdleMs` is strict, so 0ms idle survives maxIdleMs = 0.
      expect(awareness.cleanupStaleUsers(0)).toHaveLength(0);
    });

    it('evicts every user with a zero idle allowance once the clock moves', () => {
      awareness.addUser('doc-1', 's1', 'u1', 'Alice', 'alice@test');
      jest.setSystemTime(EPOCH + 1);

      expect(awareness.cleanupStaleUsers(0)).toHaveLength(1);
    });

    it('resets the idle clock when the user updates their cursor', () => {
      awareness.addUser('doc-1', 's1', 'u1', 'Alice', 'alice@test');
      jest.setSystemTime(EPOCH + MAX_IDLE_MS);
      awareness.updateCursor('s1', { index: 1, length: 0 }, null);
      jest.setSystemTime(EPOCH + MAX_IDLE_MS + 1);

      expect(awareness.cleanupStaleUsers(MAX_IDLE_MS)).toHaveLength(0);
    });

    it('resets the idle clock on refreshActivity', () => {
      awareness.addUser('doc-1', 's1', 'u1', 'Alice', 'alice@test');
      jest.setSystemTime(EPOCH + MAX_IDLE_MS);
      expect(awareness.refreshActivity('s1')).toBe(true);
      jest.setSystemTime(EPOCH + MAX_IDLE_MS + 1);

      expect(awareness.cleanupStaleUsers(MAX_IDLE_MS)).toHaveLength(0);
    });

    it('resets the idle clock when the typing flag changes', () => {
      awareness.addUser('doc-1', 's1', 'u1', 'Alice', 'alice@test');
      jest.setSystemTime(EPOCH + MAX_IDLE_MS);
      awareness.setTyping('s1', true);
      jest.setSystemTime(EPOCH + MAX_IDLE_MS + 1);

      expect(awareness.cleanupStaleUsers(MAX_IDLE_MS)).toHaveLength(0);
    });
  });

  describe('stale cleanup across documents', () => {
    it('evicts only the stale users and keeps the active ones', () => {
      awareness.addUser('doc-1', 'stale', 'u-stale', 'Stale', 's@test');
      jest.setSystemTime(EPOCH + MAX_IDLE_MS + 1);
      awareness.addUser('doc-1', 'fresh', 'u-fresh', 'Fresh', 'f@test');

      const removed = awareness.cleanupStaleUsers(MAX_IDLE_MS);

      expect(removed).toEqual([
        { socketId: 'stale', documentId: 'doc-1', userId: 'u-stale' },
      ]);
      expect(awareness.getDocumentUsers('doc-1').map((u) => u.userId)).toEqual([
        'u-fresh',
      ]);
    });

    it('drops the document state only when all of its users are evicted', () => {
      awareness.addUser('doc-empty', 's1', 'u1', 'A', 'a@test');
      awareness.addUser('doc-mixed', 's2', 'u2', 'B', 'b@test');
      jest.setSystemTime(EPOCH + MAX_IDLE_MS + 1);
      awareness.addUser('doc-mixed', 's3', 'u3', 'C', 'c@test');

      awareness.cleanupStaleUsers(MAX_IDLE_MS);

      expect(awareness.getActiveDocumentIds()).toEqual(['doc-mixed']);
    });

    it('forgets the socket mapping of an evicted user', () => {
      awareness.addUser('doc-1', 's1', 'u1', 'A', 'a@test');
      jest.setSystemTime(EPOCH + MAX_IDLE_MS + 1);

      awareness.cleanupStaleUsers(MAX_IDLE_MS);

      expect(awareness.getUserDocument('s1')).toBeNull();
      expect(awareness.refreshActivity('s1')).toBe(false);
      expect(awareness.updateCursor('s1', { index: 0, length: 0 }, null)).toBeNull();
    });

    it('stays silent when there is nothing to evict', () => {
      awareness.addUser('doc-1', 's1', 'u1', 'A', 'a@test');

      expect(awareness.cleanupStaleUsers(MAX_IDLE_MS)).toEqual([]);
      expect(logger.messages('info')).not.toContain('awareness_stale_users_cleaned');
    });

    it('logs once per sweep that evicts users', () => {
      awareness.addUser('doc-1', 's1', 'u1', 'A', 'a@test');
      awareness.addUser('doc-2', 's2', 'u2', 'B', 'b@test');
      jest.setSystemTime(EPOCH + MAX_IDLE_MS + 1);

      expect(awareness.cleanupStaleUsers(MAX_IDLE_MS)).toHaveLength(2);
      expect(
        logger.messages('info').filter((m) => m === 'awareness_stale_users_cleaned'),
      ).toHaveLength(1);
    });

    it('is a no-op on an empty service', () => {
      expect(awareness.cleanupStaleUsers(MAX_IDLE_MS)).toEqual([]);
      expect(awareness.getActiveDocumentIds()).toEqual([]);
    });
  });

  describe('colour palette boundary', () => {
    function addUsers(count: number): string[] {
      const colors: string[] = [];
      for (let i = 0; i < count; i++) {
        colors.push(
          awareness.addUser('doc-1', `s${i}`, `u${i}`, `U${i}`, `${i}@t`).color,
        );
      }
      return colors;
    }

    it.each([
      ['limit-1', PALETTE_SIZE - 1],
      ['limit', PALETTE_SIZE],
    ])('assigns %s (%i) users distinct colours', (_label, count) => {
      const colors = addUsers(count);

      expect(new Set(colors).size).toBe(count);
    });

    it('wraps around to the first colour for user limit+1', () => {
      const colors = addUsers(PALETTE_SIZE + 1);

      expect(new Set(colors).size).toBe(PALETTE_SIZE);
      expect(colors[PALETTE_SIZE]).toBe(colors[0]);
    });

    it('does not recycle the colour of a user who left', () => {
      const first = awareness.addUser('doc-1', 's1', 'u1', 'A', 'a@t').color;
      awareness.removeUser('s1');

      const second = awareness.addUser('doc-1', 's2', 'u2', 'B', 'b@t').color;

      expect(second).not.toBe(first);
    });
  });

  describe('socket identity reuse', () => {
    it('replaces the entry when the same socket is added to the same document twice', () => {
      const first = awareness.addUser('doc-1', 's1', 'u1', 'Alice', 'a@t');
      const second = awareness.addUser('doc-1', 's1', 'u1', 'Alice Renamed', 'a@t');

      expect(awareness.getDocumentUserCount('doc-1')).toBe(1);
      expect(awareness.getDocumentUsers('doc-1')[0].displayName).toBe('Alice Renamed');
      expect(second.color).not.toBe(first.color);
    });

    it('removes the socket from its previous document when it moves', () => {
      awareness.addUser('doc-old', 's1', 'u1', 'Alice', 'a@t');

      awareness.addUser('doc-new', 's1', 'u1', 'Alice', 'a@t');

      expect(awareness.getDocumentUserCount('doc-old')).toBe(0);
      expect(awareness.getActiveDocumentIds()).toEqual(['doc-new']);
      expect(awareness.getUserDocument('s1')).toBe('doc-new');
      expect(logger.messages('debug')).toContain('awareness_socket_moved_documents');
    });

    it('leaves the previous document intact when other users remain in it', () => {
      awareness.addUser('doc-old', 's1', 'u1', 'Alice', 'a@t');
      awareness.addUser('doc-old', 's2', 'u2', 'Bob', 'b@t');

      awareness.addUser('doc-new', 's1', 'u1', 'Alice', 'a@t');

      expect(awareness.getDocumentUserCount('doc-old')).toBe(1);
      expect(awareness.getActiveDocumentIds().sort()).toEqual(['doc-new', 'doc-old']);
    });

    it('allows two sockets of the same user in one document', () => {
      awareness.addUser('doc-1', 'tab-1', 'u1', 'Alice', 'a@t');
      awareness.addUser('doc-1', 'tab-2', 'u1', 'Alice', 'a@t');

      expect(awareness.getDocumentUserCount('doc-1')).toBe(2);

      awareness.removeUser('tab-1');

      expect(awareness.getDocumentUsers('doc-1').map((u) => u.userId)).toEqual(['u1']);
    });
  });

  describe('operations on removed or unknown sockets', () => {
    it('returns null the second time a socket is removed', () => {
      awareness.addUser('doc-1', 's1', 'u1', 'A', 'a@t');

      expect(awareness.removeUser('s1')).toEqual({
        documentId: 'doc-1',
        userId: 'u1',
      });
      expect(awareness.removeUser('s1')).toBeNull();
    });

    it.each([
      ['removeUser', (a: AwarenessService) => a.removeUser('nope')],
      ['updateCursor', (a: AwarenessService) => a.updateCursor('nope', null, null)],
      ['setTyping', (a: AwarenessService) => a.setTyping('nope', true)],
      ['getUserDocument', (a: AwarenessService) => a.getUserDocument('nope')],
    ])('%s returns null for an unknown socket', (_label, call) => {
      expect(call(awareness)).toBeNull();
    });

    it('refreshActivity returns false for an unknown socket', () => {
      expect(awareness.refreshActivity('nope')).toBe(false);
    });

    it('reports an empty document for an id nobody has joined', () => {
      expect(awareness.getDocumentUsers('nobody-here')).toEqual([]);
      expect(awareness.getDocumentUserCount('nobody-here')).toBe(0);
    });

    it('clears the cursor when it is set back to null', () => {
      awareness.addUser('doc-1', 's1', 'u1', 'A', 'a@t');
      awareness.updateCursor('s1', { index: 4, length: 2 }, { index: 4, length: 2 });

      const cleared = awareness.updateCursor('s1', null, null);

      expect(cleared).not.toBeNull();
      expect(cleared!.cursor).toBeNull();
      expect(cleared!.selection).toBeNull();
    });
  });
});
