import { loadConfig } from '../config';

describe('loadConfig', () => {
  const originalJwtSecret = process.env.JWT_SECRET;

  afterEach(() => {
    if (originalJwtSecret === undefined) {
      delete process.env.JWT_SECRET;
    } else {
      process.env.JWT_SECRET = originalJwtSecret;
    }
  });

  it('throws when JWT_SECRET is unset', () => {
    delete process.env.JWT_SECRET;

    expect(() => loadConfig()).toThrow(
      'JWT_SECRET environment variable is required but not set',
    );
  });

  it('throws when JWT_SECRET is a known default value', () => {
    process.env.JWT_SECRET = 'otterworks-dev-secret';

    expect(() => loadConfig()).toThrow(
      'JWT_SECRET must not be set to a known default value',
    );
  });

  it('returns the configured JWT secret', () => {
    process.env.JWT_SECRET = 'test-secret-key-for-config-tests';

    expect(loadConfig().jwt.secret).toBe('test-secret-key-for-config-tests');
  });
});
