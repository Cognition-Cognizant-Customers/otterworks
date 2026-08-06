import { RedisAdapter, RedisConfig } from '../services/redis-adapter';
import { createMockLogger, MockLogger } from './helpers/collab-harness';

/**
 * WP-11 — RedisAdapter, the untested seam every other service in this package
 * is mocked against. ioredis is replaced by a fake so no server is needed.
 */

interface FakeRedisInstance {
  options: Record<string, unknown>;
  handlers: Map<string, (...args: unknown[]) => void>;
  emit(event: string, ...args: unknown[]): void;
  [command: string]: unknown;
}

const instances: FakeRedisInstance[] = [];

jest.mock('ioredis', () => {
  const commands = [
    'connect',
    'getBuffer',
    'set',
    'setex',
    'del',
    'hset',
    'hget',
    'hgetall',
    'hdel',
    'hincrby',
    'lpush',
    'lrange',
    'ltrim',
    'llen',
    'expire',
    'publish',
    'subscribe',
    'ping',
    'disconnect',
  ];

  class FakeRedis {
    options: Record<string, unknown>;
    handlers = new Map<string, (...args: unknown[]) => void>();

    constructor(options: Record<string, unknown>) {
      this.options = options;
      for (const command of commands) {
        (this as unknown as Record<string, jest.Mock>)[command] = jest
          .fn()
          .mockResolvedValue(undefined);
      }
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      (global as any).__fakeRedisInstances.push(this);
    }

    on(event: string, handler: (...args: unknown[]) => void): this {
      this.handlers.set(event, handler);
      return this;
    }

    emit(event: string, ...args: unknown[]): void {
      this.handlers.get(event)?.(...args);
    }
  }

  return { __esModule: true, default: FakeRedis };
});

// eslint-disable-next-line @typescript-eslint/no-explicit-any
(global as any).__fakeRedisInstances = instances;

const CONFIG: RedisConfig = { host: 'redis.internal', port: 6379 };

describe('RedisAdapter (WP-11)', () => {
  let logger: MockLogger;

  function build(config = CONFIG, withLogger = true) {
    const adapter = new RedisAdapter(config, withLogger ? logger : undefined);
    const client = instances[instances.length - 2];
    const subscriber = instances[instances.length - 1];
    return { adapter, client, subscriber };
  }

  function mock(instance: FakeRedisInstance, command: string): jest.Mock {
    return instance[command] as jest.Mock;
  }

  beforeEach(() => {
    instances.length = 0;
    logger = createMockLogger();
  });

  describe('construction', () => {
    it('creates a client and a subscriber with lazy connect and a bounded retry strategy', () => {
      const { client, subscriber } = build();

      expect(instances).toHaveLength(2);
      expect(client.options).toMatchObject({
        host: 'redis.internal',
        port: 6379,
        db: 0,
        maxRetriesPerRequest: 3,
        lazyConnect: true,
      });
      expect(subscriber.options).toMatchObject({ lazyConnect: true });
      const retry = client.options.retryStrategy as (times: number) => number;
      expect(retry(1)).toBe(200);
      expect(retry(24)).toBe(4800);
      expect(retry(25)).toBe(5000);
      expect(retry(1000)).toBe(5000);
    });

    it('passes an explicit database index through', () => {
      const { client } = build({ ...CONFIG, db: 7 });

      expect(client.options.db).toBe(7);
    });

    it('logs client errors and connections', () => {
      const { client } = build();
      const err = new Error('ECONNREFUSED');

      client.emit('error', err);
      client.emit('connect');

      expect(logger.error).toHaveBeenCalledWith({ err }, 'redis_client_error');
      expect(logger.info).toHaveBeenCalledWith('redis_client_connected');
    });

    it('logs subscriber errors separately', () => {
      const { subscriber } = build();
      const err = new Error('subscriber down');

      subscriber.emit('error', err);

      expect(logger.error).toHaveBeenCalledWith({ err }, 'redis_subscriber_error');
    });

    it('tolerates being constructed without a logger', () => {
      const { client } = build(CONFIG, false);

      expect(() => client.emit('error', new Error('boom'))).not.toThrow();
      expect(() => client.emit('connect')).not.toThrow();
    });

    it('connects both connections', async () => {
      const { adapter, client, subscriber } = build();

      await adapter.connect();

      expect(mock(client, 'connect')).toHaveBeenCalled();
      expect(mock(subscriber, 'connect')).toHaveBeenCalled();
    });

    it('exposes the underlying client and subscriber', () => {
      const { adapter, client, subscriber } = build();

      expect(adapter.getClient()).toBe(client);
      expect(adapter.getSubscriber()).toBe(subscriber);
    });
  });

  describe('key prefixing', () => {
    it('leaves keys untouched when no prefix is configured', async () => {
      const { adapter, client } = build();

      await adapter.get('doc:state:a');

      expect(mock(client, 'getBuffer')).toHaveBeenCalledWith('doc:state:a');
    });

    it('prefixes every key when a prefix is configured', async () => {
      const { adapter, client } = build({ ...CONFIG, keyPrefix: 'tenant-7:' });

      await adapter.get('doc:state:a');
      await adapter.hget('doc:meta:a', 'version');
      await adapter.llen('doc:snapshots:a');

      expect(mock(client, 'getBuffer')).toHaveBeenCalledWith('tenant-7:doc:state:a');
      expect(mock(client, 'hget')).toHaveBeenCalledWith('tenant-7:doc:meta:a', 'version');
      expect(mock(client, 'llen')).toHaveBeenCalledWith('tenant-7:doc:snapshots:a');
    });

    it('does not prefix pub/sub channels', async () => {
      const { adapter, client, subscriber } = build({ ...CONFIG, keyPrefix: 'p:' });

      await adapter.publish('collab-events', 'hello');
      await adapter.subscribe('collab-events', jest.fn());

      expect(mock(client, 'publish')).toHaveBeenCalledWith('collab-events', 'hello');
      expect(mock(subscriber, 'subscribe')).toHaveBeenCalledWith('collab-events');
    });
  });

  describe('string values', () => {
    it('returns null when the key is missing', async () => {
      const { adapter, client } = build();
      mock(client, 'getBuffer').mockResolvedValueOnce(null);

      await expect(adapter.get('missing')).resolves.toBeNull();
    });

    it('returns the stored buffer when the key exists', async () => {
      const { adapter, client } = build();
      const payload = Buffer.from([1, 2, 3]);
      mock(client, 'getBuffer').mockResolvedValueOnce(payload);

      await expect(adapter.get('present')).resolves.toBe(payload);
    });

    it('uses SET when no ttl is supplied', async () => {
      const { adapter, client } = build();
      const value = Buffer.from('v');

      await adapter.set('k', value);

      expect(mock(client, 'set')).toHaveBeenCalledWith('k', value);
      expect(mock(client, 'setex')).not.toHaveBeenCalled();
    });

    it('uses SETEX for a positive ttl', async () => {
      const { adapter, client } = build();
      const value = Buffer.from('v');

      await adapter.set('k', value, 60);

      expect(mock(client, 'setex')).toHaveBeenCalledWith('k', 60, value);
      expect(mock(client, 'set')).not.toHaveBeenCalled();
    });

    it('currently falls back to SET for a ttl of 0 (boundary)', async () => {
      // `if (ttlSeconds)` treats 0 as "no expiry", so a caller asking for an
      // immediate expiry silently stores the key forever.
      const { adapter, client } = build();

      await adapter.set('k', Buffer.from('v'), 0);

      expect(mock(client, 'set')).toHaveBeenCalled();
      expect(mock(client, 'setex')).not.toHaveBeenCalled();
    });

    it('uses SETEX for the smallest positive ttl (boundary)', async () => {
      const { adapter, client } = build();

      await adapter.set('k', Buffer.from('v'), 1);

      expect(mock(client, 'setex')).toHaveBeenCalledWith('k', 1, expect.any(Buffer));
    });

    it('deletes a key', async () => {
      const { adapter, client } = build();

      await adapter.del('k');

      expect(mock(client, 'del')).toHaveBeenCalledWith('k');
    });
  });

  describe('hashes and lists', () => {
    it('round-trips hash fields', async () => {
      const { adapter, client } = build();
      mock(client, 'hget').mockResolvedValueOnce('3');
      mock(client, 'hgetall').mockResolvedValueOnce({ version: '3' });
      mock(client, 'hincrby').mockResolvedValueOnce(4);

      await adapter.hset('meta', 'version', '3');
      await expect(adapter.hget('meta', 'version')).resolves.toBe('3');
      await expect(adapter.hgetall('meta')).resolves.toEqual({ version: '3' });
      await expect(adapter.hincrby('meta', 'version', 1)).resolves.toBe(4);
      await adapter.hdel('meta', 'version');

      expect(mock(client, 'hset')).toHaveBeenCalledWith('meta', 'version', '3');
      expect(mock(client, 'hdel')).toHaveBeenCalledWith('meta', 'version');
    });

    it('returns an empty object for a missing hash', async () => {
      const { adapter, client } = build();
      mock(client, 'hgetall').mockResolvedValueOnce({});

      await expect(adapter.hgetall('nothing')).resolves.toEqual({});
    });

    it('pushes, ranges, trims and measures lists', async () => {
      const { adapter, client } = build();
      mock(client, 'lrange').mockResolvedValueOnce(['a', 'b']);
      mock(client, 'llen').mockResolvedValueOnce(2);

      await adapter.lpush('list', 'a');
      await expect(adapter.lrange('list', 0, -1)).resolves.toEqual(['a', 'b']);
      await adapter.ltrim('list', 0, 49);
      await expect(adapter.llen('list')).resolves.toBe(2);

      expect(mock(client, 'lpush')).toHaveBeenCalledWith('list', 'a');
      expect(mock(client, 'ltrim')).toHaveBeenCalledWith('list', 0, 49);
    });

    it('sets an expiry on a key', async () => {
      const { adapter, client } = build();

      await adapter.expire('k', 86400);

      expect(mock(client, 'expire')).toHaveBeenCalledWith('k', 86400);
    });
  });

  describe('pub/sub', () => {
    it('delivers messages published on the subscribed channel', async () => {
      const { adapter, subscriber } = build();
      const received: string[] = [];

      await adapter.subscribe('chan', (message) => received.push(message));
      subscriber.emit('message', 'chan', 'hello');

      expect(received).toEqual(['hello']);
    });

    it('ignores messages for a different channel', async () => {
      const { adapter, subscriber } = build();
      const received: string[] = [];

      await adapter.subscribe('chan', (message) => received.push(message));
      subscriber.emit('message', 'other-chan', 'not for us');

      expect(received).toEqual([]);
    });
  });

  describe('health and shutdown', () => {
    it('reports healthy when the server answers PONG', async () => {
      const { adapter, client } = build();
      mock(client, 'ping').mockResolvedValueOnce('PONG');

      await expect(adapter.ping()).resolves.toBe(true);
    });

    it('reports unhealthy on an unexpected reply', async () => {
      const { adapter, client } = build();
      mock(client, 'ping').mockResolvedValueOnce('LOADING');

      await expect(adapter.ping()).resolves.toBe(false);
    });

    it('reports unhealthy instead of throwing when the ping rejects', async () => {
      const { adapter, client } = build();
      mock(client, 'ping').mockRejectedValueOnce(new Error('connection lost'));

      await expect(adapter.ping()).resolves.toBe(false);
    });

    it('disconnects both connections and logs it', () => {
      const { adapter, client, subscriber } = build();

      adapter.disconnect();

      expect(mock(client, 'disconnect')).toHaveBeenCalled();
      expect(mock(subscriber, 'disconnect')).toHaveBeenCalled();
      expect(logger.info).toHaveBeenCalledWith('redis_disconnected');
    });

    it('disconnects cleanly without a logger', () => {
      const { adapter, client } = build(CONFIG, false);

      expect(() => adapter.disconnect()).not.toThrow();
      expect(mock(client, 'disconnect')).toHaveBeenCalled();
    });
  });
});
