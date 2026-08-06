import type { Logger } from 'pino';

type LogLevel = 'info' | 'warn' | 'error' | 'debug' | 'fatal' | 'trace';

export interface TestLogger {
  info: jest.Mock;
  warn: jest.Mock;
  error: jest.Mock;
  debug: jest.Mock;
  fatal: jest.Mock;
  trace: jest.Mock;
  child: jest.Mock;
  level: string;
  asLogger(): Logger;
  /** Log event names (the second pino argument) recorded at `level`. */
  messages(level: LogLevel): string[];
}

export function createTestLogger(): TestLogger {
  const logger = {
    info: jest.fn(),
    warn: jest.fn(),
    error: jest.fn(),
    debug: jest.fn(),
    fatal: jest.fn(),
    trace: jest.fn(),
    child: jest.fn(),
    level: 'silent',
    asLogger(): Logger {
      return logger as unknown as Logger;
    },
    messages(level: LogLevel): string[] {
      return logger[level].mock.calls
        .map((call: unknown[]) => call.find((arg) => typeof arg === 'string'))
        .filter((msg): msg is string => typeof msg === 'string');
    },
  };
  logger.child.mockReturnValue(logger);
  return logger;
}
