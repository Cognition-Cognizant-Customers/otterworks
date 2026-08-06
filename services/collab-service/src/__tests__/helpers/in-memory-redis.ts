import type { RedisAdapter } from '../../services/redis-adapter';

type FailableMethod = 'get' | 'set' | 'hset' | 'hincrby' | 'lpush' | 'lrange' | 'llen';

/**
 * Stateful in-memory stand-in for RedisAdapter. Unlike a jest.fn() mock it keeps
 * values, so document state genuinely survives a room being torn down and
 * re-created. Failures are injected per method rather than per call order.
 */
export class InMemoryRedis {
  readonly strings = new Map<string, Buffer>();
  readonly hashes = new Map<string, Map<string, string>>();
  readonly lists = new Map<string, string[]>();
  readonly ttls = new Map<string, number>();
  readonly calls: string[] = [];

  private readonly failures = new Map<FailableMethod, Error>();
  private readonly gates = new Map<FailableMethod, Promise<void>>();

  failOn(method: FailableMethod, error = new Error(`redis ${method} unavailable`)): void {
    this.failures.set(method, error);
  }

  clearFailure(method: FailableMethod): void {
    this.failures.delete(method);
  }

  /** Make the next calls to `method` await `gate` before completing. */
  gateOn(method: FailableMethod, gate: Promise<void>): void {
    this.gates.set(method, gate);
  }

  clearGate(method: FailableMethod): void {
    this.gates.delete(method);
  }

  private async enter(method: FailableMethod): Promise<void> {
    this.calls.push(method);
    const gate = this.gates.get(method);
    if (gate) await gate;
    const failure = this.failures.get(method);
    if (failure) throw failure;
  }

  async get(key: string): Promise<Buffer | null> {
    await this.enter('get');
    return this.strings.get(key) ?? null;
  }

  async set(key: string, value: Buffer, ttlSeconds?: number): Promise<void> {
    await this.enter('set');
    this.strings.set(key, Buffer.from(value));
    if (ttlSeconds !== undefined) this.ttls.set(key, ttlSeconds);
  }

  async del(key: string): Promise<void> {
    this.strings.delete(key);
    this.hashes.delete(key);
    this.lists.delete(key);
    this.ttls.delete(key);
  }

  private hash(key: string): Map<string, string> {
    let h = this.hashes.get(key);
    if (!h) {
      h = new Map();
      this.hashes.set(key, h);
    }
    return h;
  }

  async hset(key: string, field: string, value: string): Promise<void> {
    await this.enter('hset');
    this.hash(key).set(field, value);
  }

  async hget(key: string, field: string): Promise<string | null> {
    return this.hashes.get(key)?.get(field) ?? null;
  }

  async hgetall(key: string): Promise<Record<string, string>> {
    return Object.fromEntries(this.hashes.get(key) ?? new Map());
  }

  async hdel(key: string, field: string): Promise<void> {
    this.hashes.get(key)?.delete(field);
  }

  async hincrby(key: string, field: string, increment: number): Promise<number> {
    await this.enter('hincrby');
    const h = this.hash(key);
    const next = parseInt(h.get(field) ?? '0', 10) + increment;
    h.set(field, String(next));
    return next;
  }

  private list(key: string): string[] {
    let l = this.lists.get(key);
    if (!l) {
      l = [];
      this.lists.set(key, l);
    }
    return l;
  }

  async lpush(key: string, value: string): Promise<void> {
    await this.enter('lpush');
    this.list(key).unshift(value);
  }

  async lrange(key: string, start: number, stop: number): Promise<string[]> {
    await this.enter('lrange');
    return this.list(key).slice(start, stop + 1);
  }

  async ltrim(key: string, start: number, stop: number): Promise<void> {
    this.lists.set(key, this.list(key).slice(start, stop + 1));
  }

  async llen(key: string): Promise<number> {
    await this.enter('llen');
    return this.list(key).length;
  }

  async expire(key: string, seconds: number): Promise<void> {
    this.ttls.set(key, seconds);
  }

  async publish(): Promise<void> {}

  async subscribe(): Promise<void> {}

  async connect(): Promise<void> {}

  async ping(): Promise<boolean> {
    return true;
  }

  disconnect(): void {}

  asAdapter(): RedisAdapter {
    return this as unknown as RedisAdapter;
  }
}
