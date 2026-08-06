import { AwarenessService } from '../services/awareness';

/**
 * WP-11 — awareness edge cases: staleness boundaries, duplicate identities,
 * unknown documents and document switching.
 *
 * Determinism: the service stamps `lastActive` with `Date.now()`, so every test
 * here pins the clock with fake timers instead of relying on wall time.
 */

const FIXED_NOW = 1_700_000_000_000;
const MAX_IDLE_MS = 300_000;

function makeLogger() {
  return {
    info: jest.fn(),
    warn: jest.fn(),
    error: jest.fn(),
    debug: jest.fn(),
    fatal: jest.fn(),
    trace: jest.fn(),
    child: jest.fn().mockReturnThis(),
    level: 'info',
  } as never;
}

describe('AwarenessService — edge cases (WP-11)', () => {
  let awareness: AwarenessService;

  beforeEach(() => {
    jest.useFakeTimers({ now: FIXED_NOW });
    awareness = new AwarenessService(makeLogger());
  });

  afterEach(() => {
    jest.useRealTimers();
  });

  describe('stale eviction boundary (cleanupStaleUsers uses `now - lastActive > maxIdleMs`)', () => {
    it('keeps a user idle for maxIdleMs - 1 (limit-1)', () => {
      awareness.addUser('doc-b', 's1', 'u1', 'Alice', 'a@t.com');

      jest.setSystemTime(FIXED_NOW + MAX_IDLE_MS - 1);
      const removed = awareness.cleanupStaleUsers(MAX_IDLE_MS);

      expect(removed).toEqual([]);
      expect(awareness.getDocumentUserCount('doc-b')).toBe(1);
    });

    it('keeps a user idle for exactly maxIdleMs (limit) — the comparison is strict', () => {
      awareness.addUser('doc-b', 's1', 'u1', 'Alice', 'a@t.com');

      jest.setSystemTime(FIXED_NOW + MAX_IDLE_MS);
      const removed = awareness.cleanupStaleUsers(MAX_IDLE_MS);

      expect(removed).toEqual([]);
      expect(awareness.getDocumentUserCount('doc-b')).toBe(1);
    });

    it('evicts a user idle for maxIdleMs + 1 (limit+1)', () => {
      awareness.addUser('doc-b', 's1', 'u1', 'Alice', 'a@t.com');

      jest.setSystemTime(FIXED_NOW + MAX_IDLE_MS + 1);
      const removed = awareness.cleanupStaleUsers(MAX_IDLE_MS);

      expect(removed).toEqual([{ socketId: 's1', documentId: 'doc-b', userId: 'u1' }]);
      expect(awareness.getDocumentUserCount('doc-b')).toBe(0);
      expect(awareness.getUserDocument('s1')).toBeNull();
    });

    it('evicts only the stale user and keeps the refreshed one in the same document', () => {
      awareness.addUser('doc-b', 'stale', 'u-stale', 'Stale', 's@t.com');
      awareness.addUser('doc-b', 'active', 'u-active', 'Active', 'a@t.com');

      jest.setSystemTime(FIXED_NOW + MAX_IDLE_MS + 1);
      expect(awareness.refreshActivity('active')).toBe(true);
      const removed = awareness.cleanupStaleUsers(MAX_IDLE_MS);

      expect(removed.map((r) => r.socketId)).toEqual(['stale']);
      expect(awareness.getDocumentUserCount('doc-b')).toBe(1);
      expect(awareness.getDocumentUsers('doc-b')[0].userId).toBe('u-active');
    });

    it('evicts stale users across several documents and drops the emptied documents', () => {
      awareness.addUser('doc-1', 's1', 'u1', 'Alice', 'a@t.com');
      awareness.addUser('doc-2', 's2', 'u2', 'Bob', 'b@t.com');
      awareness.addUser('doc-3', 's3', 'u3', 'Carol', 'c@t.com');

      jest.setSystemTime(FIXED_NOW + MAX_IDLE_MS + 1);
      awareness.refreshActivity('s3');
      const removed = awareness.cleanupStaleUsers(MAX_IDLE_MS);

      expect(removed.map((r) => r.documentId).sort()).toEqual(['doc-1', 'doc-2']);
      expect(awareness.getActiveDocumentIds()).toEqual(['doc-3']);
    });

    it('is a no-op when there are no documents at all', () => {
      expect(awareness.cleanupStaleUsers(MAX_IDLE_MS)).toEqual([]);
      expect(awareness.getActiveDocumentIds()).toEqual([]);
    });

    it('evicts everyone when maxIdleMs is 0 and time has advanced by 1ms (degenerate boundary)', () => {
      awareness.addUser('doc-b', 's1', 'u1', 'Alice', 'a@t.com');

      jest.setSystemTime(FIXED_NOW + 1);

      expect(awareness.cleanupStaleUsers(0)).toHaveLength(1);
      expect(awareness.getActiveDocumentIds()).toEqual([]);
    });
  });

  describe('duplicate identities', () => {
    it('tracks two sockets of the same user id as two distinct participants', () => {
      awareness.addUser('doc-dup', 'socket-a', 'same-user', 'Alice', 'a@t.com');
      awareness.addUser('doc-dup', 'socket-b', 'same-user', 'Alice', 'a@t.com');

      expect(awareness.getDocumentUserCount('doc-dup')).toBe(2);
      expect(awareness.getDocumentUsers('doc-dup').map((u) => u.userId)).toEqual([
        'same-user',
        'same-user',
      ]);
    });

    it('removing one socket of a duplicated user leaves the other connected', () => {
      awareness.addUser('doc-dup', 'socket-a', 'same-user', 'Alice', 'a@t.com');
      awareness.addUser('doc-dup', 'socket-b', 'same-user', 'Alice', 'a@t.com');

      expect(awareness.removeUser('socket-a')).toEqual({
        documentId: 'doc-dup',
        userId: 'same-user',
      });
      expect(awareness.getDocumentUserCount('doc-dup')).toBe(1);
      expect(awareness.getUserDocument('socket-b')).toBe('doc-dup');
    });

    it('re-adding the same socket to the same document replaces its awareness record', () => {
      const first = awareness.addUser('doc-dup', 's1', 'u1', 'Alice', 'a@t.com');
      awareness.updateCursor('s1', { index: 7, length: 3 }, null);

      const second = awareness.addUser('doc-dup', 's1', 'u1', 'Alice Renamed', 'a@t.com');

      expect(awareness.getDocumentUserCount('doc-dup')).toBe(1);
      expect(second.displayName).toBe('Alice Renamed');
      expect(second.cursor).toBeNull();
      expect(second.color).not.toBe(first.color);
    });

    it('removing an already-removed socket returns null (idempotent)', () => {
      awareness.addUser('doc-dup', 's1', 'u1', 'Alice', 'a@t.com');

      expect(awareness.removeUser('s1')).not.toBeNull();
      expect(awareness.removeUser('s1')).toBeNull();
    });
  });

  describe('unknown document / unknown socket negatives', () => {
    it('reports zero users for a document nobody ever joined', () => {
      expect(awareness.getDocumentUserCount('never-seen')).toBe(0);
      expect(awareness.getDocumentUsers('never-seen')).toEqual([]);
    });

    it('rejects a cursor update from a socket that never joined', () => {
      expect(awareness.updateCursor('ghost', { index: 1, length: 0 }, null)).toBeNull();
    });

    it('rejects a typing update from a socket that never joined', () => {
      expect(awareness.setTyping('ghost', true)).toBeNull();
    });

    it('rejects an activity refresh from a socket that never joined', () => {
      expect(awareness.refreshActivity('ghost')).toBe(false);
    });

    it('rejects an activity refresh for a socket whose document was already emptied', () => {
      awareness.addUser('doc-gone', 's1', 'u1', 'Alice', 'a@t.com');
      awareness.removeUser('s1');

      expect(awareness.refreshActivity('s1')).toBe(false);
    });
  });

  describe('document switching', () => {
    it('moves a socket to the new document and empties the old one', () => {
      awareness.addUser('doc-old', 's1', 'u1', 'Alice', 'a@t.com');
      awareness.addUser('doc-new', 's1', 'u1', 'Alice', 'a@t.com');

      expect(awareness.getUserDocument('s1')).toBe('doc-new');
      expect(awareness.getDocumentUserCount('doc-old')).toBe(0);
      expect(awareness.getActiveDocumentIds()).toEqual(['doc-new']);
    });

    it('keeps the old document alive when another user is still in it', () => {
      awareness.addUser('doc-old', 's1', 'u1', 'Alice', 'a@t.com');
      awareness.addUser('doc-old', 's2', 'u2', 'Bob', 'b@t.com');
      awareness.addUser('doc-new', 's1', 'u1', 'Alice', 'a@t.com');

      expect(awareness.getDocumentUserCount('doc-old')).toBe(1);
      expect(awareness.getActiveDocumentIds().sort()).toEqual(['doc-new', 'doc-old']);
    });
  });

  describe('colour assignment boundary (20-colour palette)', () => {
    it('gives 20 concurrent users 20 distinct colours (limit)', () => {
      const colors = new Set<string>();
      for (let i = 0; i < 20; i++) {
        colors.add(
          awareness.addUser('doc-c', `s${i}`, `u${i}`, `U${i}`, 'x@t.com').color,
        );
      }

      expect(colors.size).toBe(20);
    });

    it('wraps the palette for the 21st user (limit+1)', () => {
      const colors: string[] = [];
      for (let i = 0; i < 21; i++) {
        colors.push(
          awareness.addUser('doc-c', `s${i}`, `u${i}`, `U${i}`, 'x@t.com').color,
        );
      }

      expect(colors[20]).toBe(colors[0]);
      expect(new Set(colors).size).toBe(20);
    });

    it('never recycles a colour before the palette is exhausted (limit-1)', () => {
      const colors: string[] = [];
      for (let i = 0; i < 19; i++) {
        colors.push(
          awareness.addUser('doc-c', `s${i}`, `u${i}`, `U${i}`, 'x@t.com').color,
        );
      }

      expect(new Set(colors).size).toBe(19);
    });
  });
});
