import { loadConfig } from '../config';
import { MetricsCollector } from '../metrics';

/**
 * WP-11 — configuration defaults/overrides and the metrics registry.
 *
 * `process.env` is snapshotted and restored around every test so no case
 * depends on another, or on the ambient environment.
 */

describe('loadConfig (WP-11)', () => {
  const originalEnv = process.env;

  beforeEach(() => {
    process.env = {};
  });

  afterEach(() => {
    process.env = originalEnv;
  });

  it('falls back to development defaults when nothing is set', () => {
    const config = loadConfig();

    expect(config).toEqual({
      httpPort: 8084,
      redis: {
        host: 'localhost',
        port: 6379,
        password: undefined,
        db: 0,
        keyPrefix: 'collab:',
      },
      jwt: {
        secret: 'otterworks-dev-secret',
        issuer: 'otterworks-auth-service',
      },
      cors: { origins: ['http://localhost:3000', 'http://localhost:4200'] },
      persistence: {
        intervalMs: 30000,
        snapshotIntervalMs: 300000,
        documentTtlSeconds: 86400,
        snapshotTtlSeconds: 604800,
        maxSnapshotsPerDocument: 50,
      },
      logLevel: 'info',
      otel: {
        enabled: false,
        endpoint: 'http://localhost:4318',
        serviceName: 'collab-service',
      },
    });
  });

  it('reads every value from the environment when it is set', () => {
    process.env = {
      HTTP_PORT: '9000',
      REDIS_HOST: 'redis.prod',
      REDIS_PORT: '6380',
      REDIS_PASSWORD: 's3cret',
      REDIS_DB: '3',
      REDIS_KEY_PREFIX: 'tenant-a:',
      JWT_SECRET: 'prod-secret',
      JWT_ISSUER: 'prod-issuer',
      CORS_ORIGINS: 'https://app.otterworks.test',
      PERSIST_INTERVAL_MS: '1000',
      SNAPSHOT_INTERVAL_MS: '2000',
      DOC_TTL_SECONDS: '10',
      SNAPSHOT_TTL_SECONDS: '20',
      MAX_SNAPSHOTS: '5',
      LOG_LEVEL: 'debug',
      OTEL_ENABLED: 'true',
      OTEL_EXPORTER_OTLP_ENDPOINT: 'http://collector:4318',
      OTEL_SERVICE_NAME: 'collab-prod',
    };

    const config = loadConfig();

    expect(config.httpPort).toBe(9000);
    expect(config.redis).toEqual({
      host: 'redis.prod',
      port: 6380,
      password: 's3cret',
      db: 3,
      keyPrefix: 'tenant-a:',
    });
    expect(config.jwt).toEqual({ secret: 'prod-secret', issuer: 'prod-issuer' });
    expect(config.cors.origins).toEqual(['https://app.otterworks.test']);
    expect(config.persistence).toEqual({
      intervalMs: 1000,
      snapshotIntervalMs: 2000,
      documentTtlSeconds: 10,
      snapshotTtlSeconds: 20,
      maxSnapshotsPerDocument: 5,
    });
    expect(config.logLevel).toBe('debug');
    expect(config.otel).toEqual({
      enabled: true,
      endpoint: 'http://collector:4318',
      serviceName: 'collab-prod',
    });
  });

  it('treats an empty Redis password as unset', () => {
    process.env.REDIS_PASSWORD = '';

    expect(loadConfig().redis.password).toBeUndefined();
  });

  it('splits a multi-origin CORS list', () => {
    process.env.CORS_ORIGINS = 'https://a.test,https://b.test,https://c.test';

    expect(loadConfig().cors.origins).toEqual([
      'https://a.test',
      'https://b.test',
      'https://c.test',
    ]);
  });

  it('only enables OpenTelemetry for the exact string "true"', () => {
    process.env.OTEL_ENABLED = 'TRUE';
    expect(loadConfig().otel.enabled).toBe(false);

    process.env.OTEL_ENABLED = '1';
    expect(loadConfig().otel.enabled).toBe(false);

    process.env.OTEL_ENABLED = 'true';
    expect(loadConfig().otel.enabled).toBe(true);
  });

  it('currently yields NaN for a non-numeric port rather than rejecting it', () => {
    // FINDING F10: numeric env vars are parsed with `parseInt` and never
    // validated, so a typo boots the service with NaN timings.
    process.env.HTTP_PORT = 'not-a-port';
    process.env.PERSIST_INTERVAL_MS = 'soon';

    const config = loadConfig();

    expect(Number.isNaN(config.httpPort)).toBe(true);
    expect(Number.isNaN(config.persistence.intervalMs)).toBe(true);
  });

  it.skip('should reject a non-numeric port instead of returning NaN (FINDING F10)', () => {
    process.env.HTTP_PORT = 'not-a-port';

    expect(() => loadConfig()).toThrow(/HTTP_PORT/);
  });
});

describe('MetricsCollector (WP-11)', () => {
  it('exposes the Prometheus text content type', () => {
    const metrics = new MetricsCollector();

    expect(metrics.getContentType()).toContain('text/plain');
  });

  it('renders the collaboration metrics it has recorded', async () => {
    const metrics = new MetricsCollector();
    metrics.activeConnections.inc();
    metrics.messagesTotal.inc({ type: 'join-document' });
    metrics.persistenceOperations.inc({
      operation: 'periodic_save',
      status: 'success',
    });
    metrics.persistenceDuration.observe({ operation: 'periodic_save' }, 0.01);

    const rendered = await metrics.getMetrics();

    expect(rendered).toContain('collab_active_connections 1');
    expect(rendered).toContain('collab_messages_total{type="join-document"} 1');
    expect(rendered).toContain(
      'collab_persistence_operations_total{operation="periodic_save",status="success"} 1',
    );
    expect(rendered).toContain('collab_persistence_duration_seconds_bucket');
  });

  it('keeps separate registries per collector instance', async () => {
    const first = new MetricsCollector();
    const second = new MetricsCollector();
    first.activeConnections.inc(5);

    expect(await first.getMetrics()).toContain('collab_active_connections 5');
    expect(await second.getMetrics()).toContain('collab_active_connections 0');
  });
});
