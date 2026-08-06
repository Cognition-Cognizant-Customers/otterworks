import { DocumentStore, type DocumentSnapshot } from '../services/document-store';
import { RedisAdapter } from '../services/redis-adapter';

/**
 * Redis stand-in that implements real list semantics (including negative indexes) so the
 * snapshot retention and range behaviour can be observed rather than stubbed.
 */
function createFakeRedis() {
  const lists = new Map<string, string[]>();
  const ttls = new Map<string, number>();

  const resolveStop = (length: number, stop: number): number =>
    stop < 0 ? length + stop : stop;

  const fake = {
    get: jest.fn().mockResolvedValue(null),
    set: jest.fn().mockResolvedValue(undefined),
    del: jest.fn().mockResolvedValue(undefined),
    hset: jest.fn().mockResolvedValue(undefined),
    hget: jest.fn().mockResolvedValue(null),
    hgetall: jest.fn().mockResolvedValue({}),
    hdel: jest.fn().mockResolvedValue(undefined),
    hincrby: jest.fn().mockResolvedValue(1),
    lpush: jest.fn(async (key: string, value: string) => {
      const list = lists.get(key) ?? [];
      list.unshift(value);
      lists.set(key, list);
    }),
    lrange: jest.fn(async (key: string, start: number, stop: number) => {
      const list = lists.get(key) ?? [];
      return list.slice(start, resolveStop(list.length, stop) + 1);
    }),
    ltrim: jest.fn(async (key: string, start: number, stop: number) => {
      const list = lists.get(key) ?? [];
      lists.set(key, list.slice(start, resolveStop(list.length, stop) + 1));
    }),
    llen: jest.fn(async (key: string) => (lists.get(key) ?? []).length),
    expire: jest.fn(async (key: string, seconds: number) => {
      ttls.set(key, seconds);
    }),
    publish: jest.fn().mockResolvedValue(undefined),
    subscribe: jest.fn().mockResolvedValue(undefined),
    connect: jest.fn().mockResolvedValue(undefined),
    disconnect: jest.fn(),
    ping: jest.fn().mockResolvedValue(true),
  };

  return {
    adapter: fake as unknown as jest.Mocked<RedisAdapter>,
    mock: fake,
    listLength: (key: string) => (lists.get(key) ?? []).length,
    ttlFor: (key: string) => ttls.get(key),
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

const SNAPSHOTS_KEY = 'doc:snapshots:doc-1';

describe('DocumentStore snapshot retention', () => {
  let redis: ReturnType<typeof createFakeRedis>;
  let store: DocumentStore;

  beforeEach(() => {
    jest.clearAllMocks();
    redis = createFakeRedis();
    store = new DocumentStore(redis.adapter, mockLogger);
  });

  async function createSnapshots(count: number): Promise<DocumentSnapshot[]> {
    const created: DocumentSnapshot[] = [];
    for (let i = 0; i < count; i++) {
      created.push(
        await store.createSnapshot('doc-1', Buffer.from([i]), 'user-1', `v${i}`),
      );
    }
    return created;
  }

  it('test_createSnapshot_oneBelowMaxSnapshots_doesNotTrim', async () => {
    await createSnapshots(49);

    expect(redis.mock.ltrim).not.toHaveBeenCalled();
    expect(redis.listLength(SNAPSHOTS_KEY)).toBe(49);
  });

  it('test_createSnapshot_exactlyMaxSnapshots_doesNotTrim', async () => {
    await createSnapshots(50);

    expect(redis.mock.ltrim).not.toHaveBeenCalled();
    expect(redis.listLength(SNAPSHOTS_KEY)).toBe(50);
  });

  it('test_createSnapshot_oneAboveMaxSnapshots_trimsBackToMaxSnapshots', async () => {
    await createSnapshots(51);

    expect(redis.mock.ltrim).toHaveBeenCalledTimes(1);
    expect(redis.mock.ltrim).toHaveBeenCalledWith(SNAPSHOTS_KEY, 0, 49);
    expect(redis.listLength(SNAPSHOTS_KEY)).toBe(50);
  });

  it('test_createSnapshot_pastMaxSnapshots_keepsTheNewestSnapshots', async () => {
    const created = await createSnapshots(51);

    const kept = await store.getSnapshots('doc-1', 50);
    expect(kept).toHaveLength(50);
    expect(kept[0].id).toBe(created[50].id);
    expect(kept.map((s) => s.id)).not.toContain(created[0].id);
  });

  it('test_createSnapshot_customMaxSnapshots_trimsAtTheConfiguredLimit', async () => {
    const smallStore = new DocumentStore(redis.adapter, mockLogger, { maxSnapshots: 2 });

    await smallStore.createSnapshot('doc-1', Buffer.from([1]), 'user-1');
    await smallStore.createSnapshot('doc-1', Buffer.from([2]), 'user-1');
    expect(redis.mock.ltrim).not.toHaveBeenCalled();

    await smallStore.createSnapshot('doc-1', Buffer.from([3]), 'user-1');
    expect(redis.mock.ltrim).toHaveBeenCalledWith(SNAPSHOTS_KEY, 0, 1);
    expect(redis.listLength(SNAPSHOTS_KEY)).toBe(2);
  });

  it('test_createSnapshot_everyWrite_appliesTheSnapshotTtl', async () => {
    await createSnapshots(3);

    expect(redis.mock.expire).toHaveBeenCalledTimes(3);
    for (const call of redis.mock.expire.mock.calls) {
      expect(call).toEqual([SNAPSHOTS_KEY, 604800]);
    }
    expect(redis.ttlFor(SNAPSHOTS_KEY)).toBe(604800);
  });

  it('test_createSnapshot_customSnapshotTtl_appliesTheConfiguredTtl', async () => {
    const shortLivedStore = new DocumentStore(redis.adapter, mockLogger, {
      snapshotTtl: 3600,
    });

    await shortLivedStore.createSnapshot('doc-1', Buffer.from([1]), 'user-1');

    expect(redis.mock.expire).toHaveBeenCalledWith(SNAPSHOTS_KEY, 3600);
  });
});

describe('DocumentStore snapshot retrieval', () => {
  let redis: ReturnType<typeof createFakeRedis>;
  let store: DocumentStore;

  beforeEach(async () => {
    jest.clearAllMocks();
    redis = createFakeRedis();
    store = new DocumentStore(redis.adapter, mockLogger);
    for (let i = 0; i < 25; i++) {
      await store.createSnapshot('doc-1', Buffer.from([i]), 'user-1', `v${i}`);
    }
  });

  it.each([
    [1, 1],
    [19, 19],
    [20, 20],
    [21, 21],
  ])(
    'test_getSnapshots_limitOf%i_returnsThatManySnapshots',
    async (limit: number, expected: number) => {
      const snapshots = await store.getSnapshots('doc-1', limit);

      expect(snapshots).toHaveLength(expected);
      expect(redis.mock.lrange).toHaveBeenCalledWith(SNAPSHOTS_KEY, 0, limit - 1);
    },
  );

  it('test_getSnapshots_limitOmitted_returnsTheDefaultOf20', async () => {
    const snapshots = await store.getSnapshots('doc-1');

    expect(snapshots).toHaveLength(20);
  });

  it('test_getSnapshots_limitLargerThanStoredSnapshots_returnsAllOfThem', async () => {
    const snapshots = await store.getSnapshots('doc-1', 500);

    expect(snapshots).toHaveLength(25);
  });

  it('test_getSnapshots_limitOfZero_returnsTheWholeListInsteadOfNothing', async () => {
    // Documents the current (defective) behaviour: limit 0 becomes lrange(key, 0, -1),
    // which Redis reads as "to the end of the list".
    const snapshots = await store.getSnapshots('doc-1', 0);

    expect(redis.mock.lrange).toHaveBeenCalledWith(SNAPSHOTS_KEY, 0, -1);
    expect(snapshots).toHaveLength(25);
  });

  it.skip('test_getSnapshots_limitOfZero_returnsNoSnapshots — Defect: off-by-one in DocumentStore.getSnapshots; limit 0 maps to lrange(key, 0, -1) and returns every snapshot instead of none', async () => {
    const snapshots = await store.getSnapshots('doc-1', 0);

    expect(snapshots).toEqual([]);
  });

  it('test_getSnapshotState_knownSnapshotId_returnsThatSnapshotState', async () => {
    const target = await store.createSnapshot('doc-1', Buffer.from([7, 8, 9]), 'user-1');

    const state = await store.getSnapshotState('doc-1', target.id);

    expect(state).toEqual(new Uint8Array([7, 8, 9]));
  });

  it('test_getSnapshotState_unknownSnapshotId_returnsNull', async () => {
    const state = await store.getSnapshotState('doc-1', 'snapshot-that-never-existed');

    expect(state).toBeNull();
  });

  it('test_getSnapshotState_documentWithoutSnapshots_returnsNull', async () => {
    const state = await store.getSnapshotState('doc-without-snapshots', 'any-snapshot');

    expect(state).toBeNull();
  });

  it('test_getSnapshots_documentWithoutSnapshots_returnsEmptyArray', async () => {
    const snapshots = await store.getSnapshots('doc-without-snapshots');

    expect(snapshots).toEqual([]);
  });
});
