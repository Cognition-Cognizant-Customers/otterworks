import Redis from 'ioredis';
import type { Logger } from 'pino';

export interface RedisConfig {
  host: string;
  port: number;
  password?: string;
  db?: number;
  keyPrefix?: string;
}

export class RedisAdapter {
  private readonly client: Redis;
  private readonly subscriber: Redis;
  private readonly logger: Logger | null;
  private readonly keyPrefix: string;

  constructor(config: RedisConfig, logger?: Logger) {
    this.logger = logger || null;
    this.keyPrefix = config.keyPrefix || '';

    const redisOptions = {
      host: config.host,
      port: config.port,
      password: config.password,
      db: config.db || 0,
      maxRetriesPerRequest: 3,
      retryStrategy: (times: number) => Math.min(times * 200, 5000),
      lazyConnect: true,
    };

    this.client = new Redis(redisOptions);
    this.subscriber = new Redis(redisOptions);

    this.client.on('error', (err) => {
      this.logger?.error({ err }, 'redis_client_error');
    });
    this.client.on('connect', () => {
      this.logger?.info('redis_client_connected');
    });
    this.subscriber.on('error', (err) => {
      this.logger?.error({ err }, 'redis_subscriber_error');
    });
  }

  async connect(): Promise<void> {
    await Promise.all([this.client.connect(), this.subscriber.connect()]);
  }

  getClient(): Redis {
    return this.client;
  }

  getSubscriber(): Redis {
    return this.subscriber;
  }

  private prefixKey(key: string): string {
    return `${this.keyPrefix}${key}`;
  }

  async get(key: string): Promise<Buffer | null> {
    return this.client.getBuffer(this.prefixKey(key));
  }

  async set(key: string, value: Buffer, ttlSeconds?: number): Promise<void> {
    const prefixed = this.prefixKey(key);
    if (ttlSeconds) {
      await this.client.setex(prefixed, ttlSeconds, value);
    } else {
      await this.client.set(prefixed, value);
    }
  }

  async del(key: string): Promise<void> {
    await this.client.del(this.prefixKey(key));
  }

  async hset(key: string, field: string, value: string): Promise<void> {
    await this.client.hset(this.prefixKey(key), field, value);
  }

  async hget(key: string, field: string): Promise<string | null> {
    return this.client.hget(this.prefixKey(key), field);
  }

  async hgetall(key: string): Promise<Record<string, string>> {
    return this.client.hgetall(this.prefixKey(key));
  }

  async hdel(key: string, field: string): Promise<void> {
    await this.client.hdel(this.prefixKey(key), field);
  }

  async hincrby(key: string, field: string, increment: number): Promise<number> {
    return this.client.hincrby(this.prefixKey(key), field, increment);
  }

  async lpush(key: string, value: string): Promise<void> {
    await this.client.lpush(this.prefixKey(key), value);
  }

  async lrange(key: string, start: number, stop: number): Promise<string[]> {
    return this.client.lrange(this.prefixKey(key), start, stop);
  }

  async ltrim(key: string, start: number, stop: number): Promise<void> {
    await this.client.ltrim(this.prefixKey(key), start, stop);
  }

  async llen(key: string): Promise<number> {
    return this.client.llen(this.prefixKey(key));
  }

  async expire(key: string, seconds: number): Promise<void> {
    await this.client.expire(this.prefixKey(key), seconds);
  }

  async publish(channel: string, message: string): Promise<void> {
    await this.client.publish(channel, message);
  }

  async subscribe(channel: string, callback: (message: string) => void): Promise<void> {
    await this.subscriber.subscribe(channel);
    this.subscriber.on('message', (ch, msg) => {
      if (ch === channel) callback(msg);
    });
  }

  async ping(): Promise<boolean> {
    try {
      const result = await this.client.ping();
      return result === 'PONG';
    } catch {
      return false;
    }
  }

  disconnect(): void {
    this.client.disconnect();
    this.subscriber.disconnect();
    this.logger?.info('redis_disconnected');
  }
}
