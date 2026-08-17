import type { Logger } from 'pino';

export interface CursorPosition {
  index: number;
  length: number;
}

export interface UserAwareness {
  userId: string;
  displayName: string;
  email: string;
  color: string;
  cursor: CursorPosition | null;
  selection: CursorPosition | null;
  isTyping: boolean;
  lastActive: number;
}

export interface AwarenessState {
  documentId: string;
  users: Map<string, UserAwareness>;
}

const USER_COLORS = [
  '#FF6B6B',
  '#4ECDC4',
  '#45B7D1',
  '#96CEB4',
  '#FFEAA7',
  '#DDA0DD',
  '#98D8C8',
  '#F7DC6F',
  '#BB8FCE',
  '#85C1E9',
  '#F0B27A',
  '#82E0AA',
  '#F1948A',
  '#85929E',
  '#73C6B6',
  '#E59866',
  '#AED6F1',
  '#D7BDE2',
  '#A3E4D7',
  '#FAD7A0',
];

export class AwarenessService {
  private readonly states: Map<string, AwarenessState> = new Map();
  private readonly socketToDocument: Map<string, { documentId: string; userId: string }> =
    new Map();
  private colorIndex = 0;
  private readonly logger: Logger;

  constructor(logger: Logger) {
    this.logger = logger;
  }

  private assignColor(): string {
    const color = USER_COLORS[this.colorIndex % USER_COLORS.length];
    this.colorIndex++;
    return color;
  }

  addUser(
    documentId: string,
    socketId: string,
    userId: string,
    displayName: string,
    email: string,
  ): UserAwareness {
    let state = this.states.get(documentId);
    if (!state) {
      state = { documentId, users: new Map() };
      this.states.set(documentId, state);
    }

    const awareness: UserAwareness = {
      userId,
      displayName,
      email,
      color: this.assignColor(),
      cursor: null,
      selection: null,
      isTyping: false,
      lastActive: Date.now(),
    };

    // If this socket was already in another document, clean up the old mapping
    const oldMapping = this.socketToDocument.get(socketId);
    if (oldMapping && oldMapping.documentId !== documentId) {
      const oldState = this.states.get(oldMapping.documentId);
      if (oldState) {
        oldState.users.delete(socketId);
        if (oldState.users.size === 0) {
          this.states.delete(oldMapping.documentId);
        }
      }
      this.logger.debug(
        { oldDocumentId: oldMapping.documentId, newDocumentId: documentId, socketId },
        'awareness_socket_moved_documents',
      );
    }

    state.users.set(socketId, awareness);
    this.socketToDocument.set(socketId, { documentId, userId });

    this.logger.debug({ documentId, socketId, userId }, 'awareness_user_added');

    return awareness;
  }

  removeUser(socketId: string): { documentId: string; userId: string } | null {
    const mapping = this.socketToDocument.get(socketId);
    if (!mapping) return null;

    const state = this.states.get(mapping.documentId);
    if (state) {
      state.users.delete(socketId);
      if (state.users.size === 0) {
        this.states.delete(mapping.documentId);
      }
    }

    this.socketToDocument.delete(socketId);

    this.logger.debug(
      { documentId: mapping.documentId, socketId, userId: mapping.userId },
      'awareness_user_removed',
    );

    return mapping;
  }

  updateCursor(
    socketId: string,
    cursor: CursorPosition | null,
    selection: CursorPosition | null,
  ): UserAwareness | null {
    const mapping = this.socketToDocument.get(socketId);
    if (!mapping) return null;

    const state = this.states.get(mapping.documentId);
    if (!state) return null;

    const awareness = state.users.get(socketId);
    if (!awareness) return null;

    awareness.cursor = cursor;
    awareness.selection = selection;
    awareness.lastActive = Date.now();

    return awareness;
  }

  setTyping(socketId: string, isTyping: boolean): UserAwareness | null {
    const mapping = this.socketToDocument.get(socketId);
    if (!mapping) return null;

    const state = this.states.get(mapping.documentId);
    if (!state) return null;

    const awareness = state.users.get(socketId);
    if (!awareness) return null;

    awareness.isTyping = isTyping;
    awareness.lastActive = Date.now();

    return awareness;
  }

  getDocumentUsers(documentId: string): UserAwareness[] {
    const state = this.states.get(documentId);
    if (!state) return [];
    return Array.from(state.users.values());
  }

  getDocumentUserCount(documentId: string): number {
    const state = this.states.get(documentId);
    if (!state) return 0;
    return state.users.size;
  }

  getUserDocument(socketId: string): string | null {
    const mapping = this.socketToDocument.get(socketId);
    return mapping?.documentId || null;
  }

  getActiveDocumentIds(): string[] {
    return Array.from(this.states.keys());
  }

  refreshActivity(socketId: string): boolean {
    const mapping = this.socketToDocument.get(socketId);
    if (!mapping) return false;

    const state = this.states.get(mapping.documentId);
    if (!state) return false;

    const awareness = state.users.get(socketId);
    if (!awareness) return false;

    awareness.lastActive = Date.now();
    return true;
  }

  cleanupStaleUsers(
    maxIdleMs: number,
  ): Array<{ socketId: string; documentId: string; userId: string }> {
    const removed: Array<{ socketId: string; documentId: string; userId: string }> = [];
    const now = Date.now();

    for (const [documentId, state] of this.states) {
      for (const [socketId, awareness] of state.users) {
        if (now - awareness.lastActive > maxIdleMs) {
          state.users.delete(socketId);
          this.socketToDocument.delete(socketId);
          removed.push({ socketId, documentId, userId: awareness.userId });
        }
      }
      if (state.users.size === 0) {
        this.states.delete(documentId);
      }
    }

    if (removed.length > 0) {
      this.logger.info({ count: removed.length }, 'awareness_stale_users_cleaned');
    }

    return removed;
  }
}
