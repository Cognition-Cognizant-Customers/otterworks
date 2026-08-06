import jwt from 'jsonwebtoken';
import type { Socket } from 'socket.io';
import {
  createAuthMiddleware,
  extractUserFromSocket,
  type AuthenticatedSocket,
} from '../middleware/auth';

const JWT_SECRET = 'test-secret-key-for-unit-tests';
const OTHER_SECRET = 'a-different-signing-secret';

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

function createSocket(handshake: {
  auth?: Record<string, unknown>;
  headers?: Record<string, string>;
}): AuthenticatedSocket {
  return {
    id: 'socket-1',
    handshake: {
      auth: handshake.auth ?? {},
      headers: handshake.headers ?? {},
    },
  } as unknown as AuthenticatedSocket;
}

function authenticate(socket: Socket): Error | undefined {
  let captured: Error | undefined;
  createAuthMiddleware(JWT_SECRET, mockLogger)(socket, (err?: Error) => {
    captured = err;
  });
  return captured;
}

describe('createAuthMiddleware', () => {
  beforeEach(() => {
    jest.clearAllMocks();
  });

  it('test_authMiddleware_noTokenProvided_rejectsWithAuthenticationRequired', () => {
    const error = authenticate(createSocket({}));

    expect(error?.message).toBe('Authentication required');
  });

  it('test_authMiddleware_emptyTokenProvided_rejectsWithAuthenticationRequired', () => {
    const error = authenticate(createSocket({ auth: { token: '' } }));

    expect(error?.message).toBe('Authentication required');
  });

  it('test_authMiddleware_malformedToken_rejectsWithInvalidToken', () => {
    const error = authenticate(createSocket({ auth: { token: 'not-a-jwt' } }));

    expect(error?.message).toBe('Invalid or expired token');
  });

  it('test_authMiddleware_tokenSignedWithAnotherSecret_rejectsWithInvalidToken', () => {
    const token = jwt.sign({ sub: 'user-1' }, OTHER_SECRET, { expiresIn: '1h' });

    const error = authenticate(createSocket({ auth: { token } }));

    expect(error?.message).toBe('Invalid or expired token');
  });

  it('test_authMiddleware_expiredToken_rejectsWithInvalidToken', () => {
    const token = jwt.sign({ sub: 'user-1' }, JWT_SECRET, { expiresIn: '-1h' });

    const error = authenticate(createSocket({ auth: { token } }));

    expect(error?.message).toBe('Invalid or expired token');
  });

  it('test_authMiddleware_tokenNotYetValid_rejectsWithInvalidToken', () => {
    const token = jwt.sign({ sub: 'user-1' }, JWT_SECRET, { notBefore: '1h' });

    const error = authenticate(createSocket({ auth: { token } }));

    expect(error?.message).toBe('Invalid or expired token');
  });

  it('test_authMiddleware_validToken_acceptsAndAttachesTheUser', () => {
    const token = jwt.sign(
      { sub: 'user-1', email: 'alice@test.com', name: 'Alice', roles: ['editor'] },
      JWT_SECRET,
      { expiresIn: '1h' },
    );
    const socket = createSocket({ auth: { token } });

    const error = authenticate(socket);

    expect(error).toBeUndefined();
    expect(socket.user).toEqual({
      userId: 'user-1',
      email: 'alice@test.com',
      displayName: 'Alice',
      roles: ['editor'],
    });
  });

  it('test_authMiddleware_validTokenInAuthorizationHeader_acceptsTheConnection', () => {
    const token = jwt.sign({ sub: 'user-1', name: 'Alice' }, JWT_SECRET, {
      expiresIn: '1h',
    });
    const socket = createSocket({ headers: { authorization: `Bearer ${token}` } });

    const error = authenticate(socket);

    expect(error).toBeUndefined();
    expect(socket.user?.userId).toBe('user-1');
  });

  it('test_authMiddleware_tokenWithoutNameClaim_fallsBackToDisplayName', () => {
    const token = jwt.sign({ sub: 'user-1', display_name: 'Alice Alt' }, JWT_SECRET, {
      expiresIn: '1h',
    });
    const socket = createSocket({ auth: { token } });

    authenticate(socket);

    expect(socket.user?.displayName).toBe('Alice Alt');
  });

  it('test_authMiddleware_tokenWithoutOptionalClaims_appliesAnonymousDefaults', () => {
    const token = jwt.sign({ sub: 'user-1' }, JWT_SECRET, { expiresIn: '1h' });
    const socket = createSocket({ auth: { token } });

    authenticate(socket);

    expect(socket.user).toEqual({
      userId: 'user-1',
      email: '',
      displayName: 'Anonymous',
      roles: [],
    });
  });
});

describe('extractUserFromSocket', () => {
  it('test_extractUserFromSocket_authenticatedSocket_returnsTheAuthenticatedUser', () => {
    const token = jwt.sign({ sub: 'user-1', name: 'Alice' }, JWT_SECRET, {
      expiresIn: '1h',
    });
    const socket = createSocket({ auth: { token } });
    authenticate(socket);

    expect(extractUserFromSocket(socket)).toMatchObject({
      userId: 'user-1',
      displayName: 'Alice',
    });
  });

  it('test_extractUserFromSocket_unauthenticatedSocket_returnsAnAnonymousIdentity', () => {
    const user = extractUserFromSocket(createSocket({}));

    expect(user).toEqual({
      userId: 'anon-socket-1',
      email: '',
      displayName: 'Anonymous',
      roles: [],
    });
  });
});
