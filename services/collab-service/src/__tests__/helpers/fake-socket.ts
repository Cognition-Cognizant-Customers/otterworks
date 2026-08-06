import type { Server as SocketIOServer, Socket } from 'socket.io';
import type { AuthenticatedUser } from '../../middleware/auth';

export interface RecordedEmit {
  event: string;
  payload: unknown;
}

export interface RecordedBroadcast extends RecordedEmit {
  room: string;
}

type Handler = (...args: never[]) => unknown;

/**
 * In-process stand-in for a socket.io server socket. It records everything the
 * production handlers emit and lets a test fire an inbound event at an exact
 * point in time, so ordering-sensitive scenarios need no timers or network.
 */
export class FakeSocket {
  readonly id: string;
  readonly rooms = new Set<string>();
  readonly emitted: RecordedEmit[] = [];
  readonly broadcasts: RecordedBroadcast[] = [];
  user?: AuthenticatedUser;
  connected = true;

  private readonly handlers = new Map<string, Handler>();

  constructor(id: string, user?: AuthenticatedUser) {
    this.id = id;
    this.user = user;
  }

  on(event: string, handler: Handler): this {
    this.handlers.set(event, handler);
    return this;
  }

  emit(event: string, payload?: unknown): boolean {
    this.emitted.push({ event, payload });
    return true;
  }

  to(room: string): { emit: (event: string, payload?: unknown) => boolean } {
    return {
      emit: (event: string, payload?: unknown) => {
        this.broadcasts.push({ room, event, payload });
        return true;
      },
    };
  }

  async join(room: string): Promise<void> {
    this.rooms.add(room);
  }

  leave(room: string): void {
    this.rooms.delete(room);
  }

  asSocket(): Socket {
    return this as unknown as Socket;
  }

  hasHandler(event: string): boolean {
    return this.handlers.has(event);
  }

  /** Deliver an inbound client event to the registered production handler. */
  fire(event: string, ...args: unknown[]): unknown {
    const handler = this.handlers.get(event);
    if (!handler) {
      throw new Error(`no handler registered for "${event}" on socket ${this.id}`);
    }
    return (handler as (...a: unknown[]) => unknown)(...args);
  }

  emitsOf(event: string): unknown[] {
    return this.emitted.filter((e) => e.event === event).map((e) => e.payload);
  }

  broadcastsOf(event: string): RecordedBroadcast[] {
    return this.broadcasts.filter((b) => b.event === event);
  }

  clearRecorded(): void {
    this.emitted.length = 0;
    this.broadcasts.length = 0;
  }
}

/** In-process stand-in for the socket.io server namespace used by the handlers. */
export class FakeIO {
  readonly emitted: RecordedBroadcast[] = [];
  readonly sockets = { sockets: new Map<string, FakeSocket>() };

  private connectionHandler: ((socket: Socket) => void) | null = null;

  on(event: string, handler: (socket: Socket) => void): this {
    if (event === 'connection') this.connectionHandler = handler;
    return this;
  }

  to(room: string): { emit: (event: string, payload?: unknown) => boolean } {
    return {
      emit: (event: string, payload?: unknown) => {
        this.emitted.push({ room, event, payload });
        return true;
      },
    };
  }

  asServer(): SocketIOServer {
    return this as unknown as SocketIOServer;
  }

  /** Make a socket addressable via `io.sockets.sockets` without firing `connection`. */
  register(socket: FakeSocket): FakeSocket {
    this.sockets.sockets.set(socket.id, socket);
    return socket;
  }

  connect(socket: FakeSocket): FakeSocket {
    if (!this.connectionHandler) {
      throw new Error('no connection handler registered — call manager.start() first');
    }
    this.register(socket);
    this.connectionHandler(socket.asSocket());
    return socket;
  }

  /** Simulate a transport-level disconnect with an explicit socket.io reason. */
  disconnect(socket: FakeSocket, reason = 'client namespace disconnect'): void {
    this.sockets.sockets.delete(socket.id);
    socket.connected = false;
    socket.fire('disconnect', reason);
  }

  emitsOf(event: string): RecordedBroadcast[] {
    return this.emitted.filter((e) => e.event === event);
  }

  clearRecorded(): void {
    this.emitted.length = 0;
  }
}
