import { RedisAdapter } from '../services/redis-adapter';

type Handler = (...args: unknown[]) => void;

interface FakeRedisClient {
  options: Record<string, unknown>;
  handlers: Map<string, Handler[]>;
  on: jest.Mock;
  connect: jest.Mock;
  getBuffer: jest.Mock;
  set: jest.Mock;
  setex: jest.Mock;
  del: jest.Mock;
  hset: jest.Mock;
  hget: jest.Mock;
  hgetall: jest.Mock;
  hdel: jest.Mock;
  hincrby: jest.Mock;
  lpush: jest.Mock;
  lrange: jest.Mock;
  ltrim: jest.Mock;
  llen: jest.Mock;
  expire: jest.Mock;
  publish: jest.Mock;
  subscribe: jest.Mock;
  ping: jest.Mock;
  disconnect: jest.Mock;
  emit: (event: string, ...args: unknown[]) => void;
}

const createdClients: FakeRedisClient[] = [];

jest.mock('ioredis', () => {
  return {
    __esModule: true,
    default: jest.fn().mockImplementation((options: Record<string, unknown>) => {
      const handlers = new Map<string, Handler[]>();
      const client: FakeRedisClient = {
        options,
        handlers,
        on: jest.fn((event: string, handler: Handler) => {
          handlers.set(event, [...(handlers.get(event) ?? []), handler]);
          return client;
        }),
        connect: jest.fn().mockResolvedValue(undefined),
        getBuffer: jest.fn().mockResolvedValue(null),
        set: jest.fn().mockResolvedValue('OK'),
        setex: jest.fn().mockResolvedValue('OK'),
        del: jest.fn().mockResolvedValue(1),
        hset: jest.fn().mockResolvedValue(1),
        hget: jest.fn().mockResolvedValue(null),
        hgetall: jest.fn().mockResolvedValue({}),
        hdel: jest.fn().mockResolvedValue(1),
        hincrby: jest.fn().mockResolvedValue(1),
        lpush: jest.fn().mockResolvedValue(1),
        lrange: jest.fn().mockResolvedValue([]),
        ltrim: jest.fn().mockResolvedValue('OK'),
        llen: jest.fn().mockResolvedValue(0),
        expire: jest.fn().mockResolvedValue(1),
        publish: jest.fn().mockResolvedValue(1),
        subscribe: jest.fn().mockResolvedValue(1),
        ping: jest.fn().mockResolvedValue('PONG'),
        disconnect: jest.fn(),
        emit: (event: string, ...args: unknown[]) => {
          for (const handler of handlers.get(event) ?? []) handler(...args);
        },
      };
      createdClients.push(client);
      return client;
    }),
  };
});

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

describe('RedisAdapter', () => {
  let adapter: RedisAdapter;
  let client: FakeRedisClient;
  let subscriber: FakeRedisClient;

  beforeEach(() => {
    jest.clearAllMocks();
    createdClients.length = 0;
    adapter = new RedisAdapter({ host: 'redis-host', port: 6380, db: 2 }, mockLogger);
    [client, subscriber] = createdClients;
  });

  describe('connection lifecycle', () => {
    it('test_constructor_always_createsSeparateClientAndSubscriberConnections', () => {
      expect(createdClients).toHaveLength(2);
      expect(adapter.getClient()).not.toBe(adapter.getSubscriber());
    });

    it('test_constructor_always_configuresTheRequestedEndpointLazily', () => {
      expect(client.options).toMatchObject({
        host: 'redis-host',
        port: 6380,
        db: 2,
        lazyConnect: true,
      });
    });

    it('test_connect_always_connectsBothClientAndSubscriber', async () => {
      await adapter.connect();

      expect(client.connect).toHaveBeenCalledTimes(1);
      expect(subscriber.connect).toHaveBeenCalledTimes(1);
    });

    it('test_connect_clientConnectionFails_rejects', async () => {
      client.connect.mockRejectedValueOnce(new Error('connection refused'));

      await expect(adapter.connect()).rejects.toThrow('connection refused');
    });

    it('test_clientError_always_isHandledWithoutThrowing', () => {
      expect(() => client.emit('error', new Error('boom'))).not.toThrow();
      expect(() => subscriber.emit('error', new Error('boom'))).not.toThrow();
    });

    it('test_disconnect_always_disconnectsBothConnections', () => {
      adapter.disconnect();

      expect(client.disconnect).toHaveBeenCalledTimes(1);
      expect(subscriber.disconnect).toHaveBeenCalledTimes(1);
    });

    it('test_ping_serverResponds_returnsTrue', async () => {
      await expect(adapter.ping()).resolves.toBe(true);
    });

    it('test_ping_unexpectedResponse_returnsFalse', async () => {
      client.ping.mockResolvedValueOnce('NOPE');

      await expect(adapter.ping()).resolves.toBe(false);
    });

    it('test_ping_serverUnreachable_returnsFalseInsteadOfThrowing', async () => {
      client.ping.mockRejectedValueOnce(new Error('no connection'));

      await expect(adapter.ping()).resolves.toBe(false);
    });
  });

  describe('key/value operations', () => {
    it('test_get_keyMissing_returnsNull', async () => {
      await expect(adapter.get('doc:1')).resolves.toBeNull();
      expect(client.getBuffer).toHaveBeenCalledWith('doc:1');
    });

    it('test_get_keyPresent_returnsStoredBuffer', async () => {
      const stored = Buffer.from([1, 2, 3]);
      client.getBuffer.mockResolvedValueOnce(stored);

      await expect(adapter.get('doc:1')).resolves.toBe(stored);
    });

    it('test_set_withoutTtl_writesWithoutExpiry', async () => {
      const value = Buffer.from([9]);

      await adapter.set('doc:1', value);

      expect(client.set).toHaveBeenCalledWith('doc:1', value);
      expect(client.setex).not.toHaveBeenCalled();
    });

    it('test_set_withTtl_writesWithExpiry', async () => {
      const value = Buffer.from([9]);

      await adapter.set('doc:1', value, 120);

      expect(client.setex).toHaveBeenCalledWith('doc:1', 120, value);
      expect(client.set).not.toHaveBeenCalled();
    });

    it('test_del_always_deletesTheKey', async () => {
      await adapter.del('doc:1');

      expect(client.del).toHaveBeenCalledWith('doc:1');
    });

    it('test_hashOperations_always_targetTheRequestedKeyAndField', async () => {
      await adapter.hset('meta:1', 'version', '2');
      await adapter.hget('meta:1', 'version');
      await adapter.hgetall('meta:1');
      await adapter.hdel('meta:1', 'version');
      await adapter.hincrby('meta:1', 'version', 1);

      expect(client.hset).toHaveBeenCalledWith('meta:1', 'version', '2');
      expect(client.hget).toHaveBeenCalledWith('meta:1', 'version');
      expect(client.hgetall).toHaveBeenCalledWith('meta:1');
      expect(client.hdel).toHaveBeenCalledWith('meta:1', 'version');
      expect(client.hincrby).toHaveBeenCalledWith('meta:1', 'version', 1);
    });

    it('test_listOperations_always_targetTheRequestedKey', async () => {
      await adapter.lpush('snap:1', 'payload');
      await adapter.lrange('snap:1', 0, 9);
      await adapter.ltrim('snap:1', 0, 4);
      await adapter.llen('snap:1');
      await adapter.expire('snap:1', 60);

      expect(client.lpush).toHaveBeenCalledWith('snap:1', 'payload');
      expect(client.lrange).toHaveBeenCalledWith('snap:1', 0, 9);
      expect(client.ltrim).toHaveBeenCalledWith('snap:1', 0, 4);
      expect(client.llen).toHaveBeenCalledWith('snap:1');
      expect(client.expire).toHaveBeenCalledWith('snap:1', 60);
    });
  });

  describe('key prefixing', () => {
    it('test_keyPrefixConfigured_always_prefixesEveryKey', async () => {
      createdClients.length = 0;
      const prefixed = new RedisAdapter(
        { host: 'redis-host', port: 6379, keyPrefix: 'tenant-a:' },
        mockLogger,
      );
      const [prefixedClient] = createdClients;

      await prefixed.get('doc:1');
      await prefixed.set('doc:1', Buffer.from([1]), 30);
      await prefixed.llen('snap:1');

      expect(prefixedClient.getBuffer).toHaveBeenCalledWith('tenant-a:doc:1');
      expect(prefixedClient.setex).toHaveBeenCalledWith(
        'tenant-a:doc:1',
        30,
        expect.any(Buffer),
      );
      expect(prefixedClient.llen).toHaveBeenCalledWith('tenant-a:snap:1');
    });
  });

  describe('pub/sub', () => {
    it('test_publish_always_publishesOnTheClientConnection', async () => {
      await adapter.publish('collab:doc-1', 'hello');

      expect(client.publish).toHaveBeenCalledWith('collab:doc-1', 'hello');
      expect(subscriber.publish).not.toHaveBeenCalled();
    });

    it('test_subscribe_messageOnSubscribedChannel_invokesTheCallback', async () => {
      const received: string[] = [];

      await adapter.subscribe('collab:doc-1', (message) => received.push(message));
      subscriber.emit('message', 'collab:doc-1', 'update-1');

      expect(subscriber.subscribe).toHaveBeenCalledWith('collab:doc-1');
      expect(received).toEqual(['update-1']);
    });

    it('test_subscribe_messageOnAnotherChannel_doesNotInvokeTheCallback', async () => {
      const received: string[] = [];

      await adapter.subscribe('collab:doc-1', (message) => received.push(message));
      subscriber.emit('message', 'collab:doc-2', 'update-for-other-doc');

      expect(received).toEqual([]);
    });

    it('test_subscribe_multipleChannels_routesEachMessageToItsOwnCallback', async () => {
      const first: string[] = [];
      const second: string[] = [];

      await adapter.subscribe('channel-1', (message) => first.push(message));
      await adapter.subscribe('channel-2', (message) => second.push(message));
      subscriber.emit('message', 'channel-2', 'to-second');

      expect(first).toEqual([]);
      expect(second).toEqual(['to-second']);
    });
  });

  describe('optional logger', () => {
    it('test_constructedWithoutLogger_clientError_doesNotThrow', () => {
      createdClients.length = 0;
      const silent = new RedisAdapter({ host: 'redis-host', port: 6379 });
      const [silentClient] = createdClients;

      expect(() => silentClient.emit('error', new Error('boom'))).not.toThrow();
      expect(() => silentClient.emit('connect')).not.toThrow();
      expect(() => silent.disconnect()).not.toThrow();
    });
  });
});
