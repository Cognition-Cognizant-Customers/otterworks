export interface Config {
  httpPort: number;
  redis: {
    host: string;
    port: number;
    password: string | undefined;
    db: number;
    keyPrefix: string;
  };
  jwt: {
    secret: string;
    issuer: string;
  };
  cors: {
    origins: string[];
  };
  persistence: {
    intervalMs: number;
    snapshotIntervalMs: number;
    documentTtlSeconds: number;
    snapshotTtlSeconds: number;
    maxSnapshotsPerDocument: number;
  };
  logLevel: string;
  otel: {
    enabled: boolean;
    endpoint: string;
    serviceName: string;
  };
}

export function loadConfig(): Config {
  return {
    httpPort: Number.parseInt(process.env.HTTP_PORT || '8084', 10),
    redis: {
      host: process.env.REDIS_HOST || 'localhost',
      port: Number.parseInt(process.env.REDIS_PORT || '6379', 10),
      password: process.env.REDIS_PASSWORD || undefined,
      db: Number.parseInt(process.env.REDIS_DB || '0', 10),
      keyPrefix: process.env.REDIS_KEY_PREFIX || 'collab:',
    },
    jwt: {
      secret: process.env.JWT_SECRET || 'otterworks-dev-secret',
      issuer: process.env.JWT_ISSUER || 'otterworks-auth-service',
    },
    cors: {
      origins: (
        process.env.CORS_ORIGINS || 'http://localhost:3000,http://localhost:4200'
      ).split(','),
    },
    persistence: {
      intervalMs: Number.parseInt(process.env.PERSIST_INTERVAL_MS || '30000', 10),
      snapshotIntervalMs: Number.parseInt(process.env.SNAPSHOT_INTERVAL_MS || '300000', 10),
      documentTtlSeconds: Number.parseInt(process.env.DOC_TTL_SECONDS || '86400', 10),
      snapshotTtlSeconds: Number.parseInt(process.env.SNAPSHOT_TTL_SECONDS || '604800', 10),
      maxSnapshotsPerDocument: Number.parseInt(process.env.MAX_SNAPSHOTS || '50', 10),
    },
    logLevel: process.env.LOG_LEVEL || 'info',
    otel: {
      enabled: process.env.OTEL_ENABLED === 'true',
      endpoint: process.env.OTEL_EXPORTER_OTLP_ENDPOINT || 'http://localhost:4318',
      serviceName: process.env.OTEL_SERVICE_NAME || 'collab-service',
    },
  };
}
