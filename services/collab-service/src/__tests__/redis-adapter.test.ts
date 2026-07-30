const mockRedisInstances: MockRedis[] = [];

interface MockRedis {
  options: Record<string, unknown>;
  handlers: Record<string, Array<(...args: unknown[]) => void>>;
  connect: jest.Mock;
  disconnect: jest.Mock;
  on: jest.Mock;
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
}

jest.mock('ioredis', () => {
  return jest.fn().mockImplementation((options: Record<string, unknown>) => {
    const handlers: Record<string, Array<(...args: unknown[]) => void>> = {};
    const instance: MockRedis = {
      options,
      handlers,
      connect: jest.fn().mockResolvedValue(undefined),
      disconnect: jest.fn(),
      on: jest.fn((event: string, cb: (...args: unknown[]) => void) => {
        handlers[event] = handlers[event] || [];
        handlers[event].push(cb);
        return instance;
      }),
      getBuffer: jest.fn().mockResolvedValue(Buffer.from('value')),
      set: jest.fn().mockResolvedValue('OK'),
      setex: jest.fn().mockResolvedValue('OK'),
      del: jest.fn().mockResolvedValue(1),
      hset: jest.fn().mockResolvedValue(1),
      hget: jest.fn().mockResolvedValue('field-value'),
      hgetall: jest.fn().mockResolvedValue({ a: '1' }),
      hdel: jest.fn().mockResolvedValue(1),
      hincrby: jest.fn().mockResolvedValue(5),
      lpush: jest.fn().mockResolvedValue(1),
      lrange: jest.fn().mockResolvedValue(['x', 'y']),
      ltrim: jest.fn().mockResolvedValue('OK'),
      llen: jest.fn().mockResolvedValue(2),
      expire: jest.fn().mockResolvedValue(1),
      publish: jest.fn().mockResolvedValue(0),
      subscribe: jest.fn().mockResolvedValue(1),
      ping: jest.fn().mockResolvedValue('PONG'),
    };
    mockRedisInstances.push(instance);
    return instance;
  });
});

import { RedisAdapter } from '../services/redis-adapter';

describe('RedisAdapter', () => {
  const config = { host: 'localhost', port: 6379, keyPrefix: 'collab:' };
  let adapter: RedisAdapter;
  let client: MockRedis;
  let subscriber: MockRedis;

  beforeEach(() => {
    jest.clearAllMocks();
    mockRedisInstances.length = 0;
    adapter = new RedisAdapter(config);
    [client, subscriber] = mockRedisInstances;
  });

  it('creates a client and a subscriber with the given options', () => {
    expect(mockRedisInstances).toHaveLength(2);
    expect(client.options).toMatchObject({
      host: 'localhost',
      port: 6379,
      db: 0,
      lazyConnect: true,
    });
  });

  it('connects both connections', async () => {
    await adapter.connect();
    expect(client.connect).toHaveBeenCalled();
    expect(subscriber.connect).toHaveBeenCalled();
  });

  it('prefixes keys on get/set/del', async () => {
    await adapter.get('doc-1');
    expect(client.getBuffer).toHaveBeenCalledWith('collab:doc-1');

    await adapter.set('doc-1', Buffer.from('v'));
    expect(client.set).toHaveBeenCalledWith('collab:doc-1', Buffer.from('v'));

    await adapter.del('doc-1');
    expect(client.del).toHaveBeenCalledWith('collab:doc-1');
  });

  it('uses setex when a TTL is provided', async () => {
    await adapter.set('doc-1', Buffer.from('v'), 60);
    expect(client.setex).toHaveBeenCalledWith('collab:doc-1', 60, Buffer.from('v'));
    expect(client.set).not.toHaveBeenCalled();
  });

  it('prefixes keys for hash operations', async () => {
    await adapter.hset('h', 'f', 'v');
    expect(client.hset).toHaveBeenCalledWith('collab:h', 'f', 'v');

    await expect(adapter.hget('h', 'f')).resolves.toBe('field-value');
    expect(client.hget).toHaveBeenCalledWith('collab:h', 'f');

    await expect(adapter.hgetall('h')).resolves.toEqual({ a: '1' });
    await adapter.hdel('h', 'f');
    expect(client.hdel).toHaveBeenCalledWith('collab:h', 'f');

    await expect(adapter.hincrby('h', 'f', 2)).resolves.toBe(5);
    expect(client.hincrby).toHaveBeenCalledWith('collab:h', 'f', 2);
  });

  it('prefixes keys for list operations', async () => {
    await adapter.lpush('l', 'v');
    expect(client.lpush).toHaveBeenCalledWith('collab:l', 'v');

    await expect(adapter.lrange('l', 0, -1)).resolves.toEqual(['x', 'y']);
    expect(client.lrange).toHaveBeenCalledWith('collab:l', 0, -1);

    await adapter.ltrim('l', 0, 9);
    expect(client.ltrim).toHaveBeenCalledWith('collab:l', 0, 9);

    await expect(adapter.llen('l')).resolves.toBe(2);
    await adapter.expire('l', 30);
    expect(client.expire).toHaveBeenCalledWith('collab:l', 30);
  });

  it('publishes without prefixing the channel', async () => {
    await adapter.publish('channel-1', 'msg');
    expect(client.publish).toHaveBeenCalledWith('channel-1', 'msg');
  });

  it('subscribes and only invokes the callback for the matching channel', async () => {
    const callback = jest.fn();
    await adapter.subscribe('channel-1', callback);
    expect(subscriber.subscribe).toHaveBeenCalledWith('channel-1');

    const messageHandlers = subscriber.handlers['message'] || [];
    for (const handler of messageHandlers) {
      handler('channel-1', 'hello');
      handler('other-channel', 'ignored');
    }

    expect(callback).toHaveBeenCalledTimes(1);
    expect(callback).toHaveBeenCalledWith('hello');
  });

  it('ping returns true on PONG and false on error', async () => {
    await expect(adapter.ping()).resolves.toBe(true);

    client.ping.mockRejectedValueOnce(new Error('down'));
    await expect(adapter.ping()).resolves.toBe(false);
  });

  it('disconnects both connections', () => {
    adapter.disconnect();
    expect(client.disconnect).toHaveBeenCalled();
    expect(subscriber.disconnect).toHaveBeenCalled();
  });
});
