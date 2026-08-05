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

const KNOWN_INSECURE_JWT_SECRETS = new Set([
  'otterworks-dev-secret',
  'changeme',
  'secret',
]);

function requireJwtSecret(): string {
  const secret = process.env.JWT_SECRET;
  if (!secret) {
    throw new Error('JWT_SECRET environment variable is required but not set');
  }
  if (KNOWN_INSECURE_JWT_SECRETS.has(secret)) {
    throw new Error('JWT_SECRET must not be set to a known default value');
  }
  return secret;
}

export function loadConfig(): Config {
  return {
    httpPort: parseInt(process.env.HTTP_PORT || '8084', 10),
    redis: {
      host: process.env.REDIS_HOST || 'localhost',
      port: parseInt(process.env.REDIS_PORT || '6379', 10),
      password: process.env.REDIS_PASSWORD || undefined,
      db: parseInt(process.env.REDIS_DB || '0', 10),
      keyPrefix: process.env.REDIS_KEY_PREFIX || 'collab:',
    },
    jwt: {
      secret: requireJwtSecret(),
      issuer: process.env.JWT_ISSUER || 'otterworks-auth-service',
    },
    cors: {
      origins: (
        process.env.CORS_ORIGINS || 'http://localhost:3000,http://localhost:4200'
      ).split(','),
    },
    persistence: {
      intervalMs: parseInt(process.env.PERSIST_INTERVAL_MS || '30000', 10),
      snapshotIntervalMs: parseInt(process.env.SNAPSHOT_INTERVAL_MS || '300000', 10),
      documentTtlSeconds: parseInt(process.env.DOC_TTL_SECONDS || '86400', 10),
      snapshotTtlSeconds: parseInt(process.env.SNAPSHOT_TTL_SECONDS || '604800', 10),
      maxSnapshotsPerDocument: parseInt(process.env.MAX_SNAPSHOTS || '50', 10),
    },
    logLevel: process.env.LOG_LEVEL || 'info',
    otel: {
      enabled: process.env.OTEL_ENABLED === 'true',
      endpoint: process.env.OTEL_EXPORTER_OTLP_ENDPOINT || 'http://localhost:4318',
      serviceName: process.env.OTEL_SERVICE_NAME || 'collab-service',
    },
  };
}
