import { Server as SocketIOServer, Socket } from 'socket.io';
import type { Logger } from 'pino';
import * as Y from 'yjs';
import { DocumentStore } from '../services/document-store';
import { AwarenessService, type CursorPosition } from '../services/awareness';
import { extractUserFromSocket } from '../middleware/auth';
import { MetricsCollector } from '../metrics';
import { PresenceHandler } from './presence';

export interface CommentAnnotation {
  id: string;
  documentId: string;
  threadId: string;
  content: string;
  author: { userId: string; displayName: string };
  rangeStart: number;
  rangeEnd: number;
  createdAt: string;
  parentId?: string;
}

export interface CollaborationDeps {
  io: SocketIOServer;
  documentStore: DocumentStore;
  awareness: AwarenessService;
  presenceHandler: PresenceHandler;
  metrics: MetricsCollector;
  logger: Logger;
  persistIntervalMs: number;
  snapshotIntervalMs: number;
}

export class CollaborationManager {
  private readonly documents: Map<string, Y.Doc> = new Map();
  private readonly documentInitPromises: Map<string, Promise<Y.Doc>> = new Map();
  private readonly cleaningUp: Set<string> = new Set();
  private readonly deps: CollaborationDeps;
  private persistTimer: NodeJS.Timeout | null = null;
  private snapshotTimer: NodeJS.Timeout | null = null;

  constructor(deps: CollaborationDeps) {
    this.deps = deps;
  }

  getDocument(documentId: string): Y.Doc | undefined {
    return this.documents.get(documentId);
  }

  getDocumentCount(): number {
    return this.documents.size;
  }

  start(): void {
    const { io, logger } = this.deps;

    io.on('connection', (socket: Socket) => {
      this.deps.metrics.activeConnections.inc();
      logger.info({ socketId: socket.id }, 'client_connected');

      this.registerSocketHandlers(socket);

      socket.on('disconnect', (reason) => {
        this.handleDisconnect(socket, reason);
      });
    });

    this.startPersistenceLoop();
    this.startSnapshotLoop();
    logger.info('collaboration_manager_started');
  }

  async stop(): Promise<void> {
    if (this.persistTimer) clearInterval(this.persistTimer);
    if (this.snapshotTimer) clearInterval(this.snapshotTimer);

    // Final persistence pass: flush all in-memory documents to Redis before shutdown
    const { documentStore, logger } = this.deps;
    for (const [documentId, doc] of this.documents) {
      try {
        const state = Y.encodeStateAsUpdate(doc);
        await documentStore.saveDocumentState(documentId, Buffer.from(state));
        logger.info({ documentId }, 'document_persisted_on_shutdown');
      } catch (err) {
        logger.error({ err, documentId }, 'document_persist_on_shutdown_failed');
      }
    }

    logger.info('collaboration_manager_stopped');
  }

  private registerSocketHandlers(socket: Socket): void {
    socket.on('join-document', (data, ack) => this.handleJoinDocument(socket, data, ack));
    socket.on('leave-document', (data) => this.handleLeaveDocument(socket, data));
    socket.on('document-update', (data) => this.handleDocumentUpdate(socket, data));
    socket.on('cursor-update', (data) => this.handleCursorUpdate(socket, data));
    socket.on('typing-indicator', (data) => this.handleTypingIndicator(socket, data));
    socket.on('comment-add', (data) => this.handleCommentAdd(socket, data));
    socket.on('comment-update', (data) => this.handleCommentUpdate(socket, data));
    socket.on('comment-delete', (data) => this.handleCommentDelete(socket, data));
    socket.on('request-snapshot', (data) => this.handleRequestSnapshot(socket, data));
    socket.on('request-history', (data) => this.handleRequestHistory(socket, data));
  }

  private async handleJoinDocument(
    socket: Socket,
    data: { documentId: string },
    ack?: (response: { success: boolean; error?: string }) => void,
  ): Promise<void> {
    const { documentId } = data;
    const { io, awareness, presenceHandler, metrics, logger } = this.deps;
    const user = extractUserFromSocket(socket);
    const room = `doc:${documentId}`;

    try {
      // If socket is already in another document, leave it first
      const oldDocId = awareness.getUserDocument(socket.id);
      if (oldDocId && oldDocId !== documentId) {
        const oldRoom = `doc:${oldDocId}`;
        socket.leave(oldRoom);
        awareness.removeUser(socket.id);
        socket.to(oldRoom).emit('user-left', {
          socketId: socket.id,
          userId: user.userId,
        });
        presenceHandler.broadcastPresenceUpdate(io, oldDocId);
        if (awareness.getDocumentUserCount(oldDocId) === 0) {
          this.persistAndCleanupDocument(oldDocId);
        }
        logger.info(
          { oldDocumentId: oldDocId, newDocumentId: documentId, socketId: socket.id },
          'user_switched_documents',
        );
      }

      await socket.join(room);
      logger.info(
        { documentId, userId: user.userId, socketId: socket.id },
        'user_joined_document',
      );

      // Get or create Yjs document (safe against concurrent joins)
      const doc = await this.getOrCreateDoc(documentId);

      // Register awareness
      const userAwareness = awareness.addUser(
        documentId,
        socket.id,
        user.userId,
        user.displayName,
        user.email,
      );

      // Sync document state to joining client
      const syncStart = Date.now();
      const state = Y.encodeStateAsUpdate(doc);
      socket.emit('sync-document', {
        documentId,
        state: Buffer.from(state).toString('base64'),
      });
      metrics.documentSyncDuration.observe((Date.now() - syncStart) / 1000);

      // Notify others
      socket.to(room).emit('user-joined', {
        userId: user.userId,
        displayName: user.displayName,
        color: userAwareness.color,
        socketId: socket.id,
      });

      // Send current presence to joining user
      presenceHandler.broadcastPresenceUpdate(io, documentId);
      metrics.messagesTotal.inc({ type: 'join-document' });

      if (ack) ack({ success: true });
    } catch (err) {
      logger.error({ err, documentId, socketId: socket.id }, 'join_document_failed');
      socket.leave(room);
      metrics.connectionErrors.inc({ reason: 'join_failed' });
      if (ack) ack({ success: false, error: 'Failed to join document' });
    }
  }

  private handleLeaveDocument(socket: Socket, data: { documentId: string }): void {
    const { io, awareness, presenceHandler, metrics, logger } = this.deps;

    const mapping = awareness.removeUser(socket.id);
    // Use the document the awareness service actually tracked, falling back to client-provided id
    const trackedDocId = mapping?.documentId ?? data.documentId;
    const room = `doc:${trackedDocId}`;

    socket.leave(room);

    if (mapping) {
      socket.to(room).emit('user-left', { socketId: socket.id, userId: mapping.userId });
      presenceHandler.broadcastPresenceUpdate(io, trackedDocId);
    }
    metrics.messagesTotal.inc({ type: 'leave-document' });

    // Clean up empty documents from memory
    const userCount = awareness.getDocumentUserCount(trackedDocId);
    if (userCount === 0) {
      this.persistAndCleanupDocument(trackedDocId);
    }

    logger.info({ documentId: trackedDocId, socketId: socket.id }, 'user_left_document');
  }

  private async handleDocumentUpdate(
    socket: Socket,
    data: { documentId: string; update: unknown },
  ): Promise<void> {
    const { documentStore, metrics, logger } = this.deps;
    const { documentId, update } = data;
    const room = `doc:${documentId}`;
    const user = extractUserFromSocket(socket);

    const doc = this.documents.get(documentId);
    if (!doc) {
      logger.warn({ documentId, socketId: socket.id }, 'document_update_for_unknown_doc');
      return;
    }

    // Accept legacy JSON patches for API-flow clients while preserving Yjs updates for real editors.
    if (typeof update === 'string') {
      try {
        const updateBytes = Buffer.from(update, 'base64');
        Y.applyUpdate(doc, new Uint8Array(updateBytes));
      } catch (err) {
        logger.error({ err, documentId, socketId: socket.id }, 'crdt_apply_failed');
        socket.emit('document-update-error', {
          documentId,
          error: 'Failed to apply update',
        });
        return;
      }
    }

    // Refresh the user's lastActive so active editors aren't evicted as stale
    this.deps.awareness.refreshActivity(socket.id);

    // Step 2: Broadcast to other clients immediately after successful CRDT apply
    socket.to(room).emit('document-update', { documentId, update });
    metrics.documentUpdatesTotal.inc();
    metrics.messagesTotal.inc({ type: 'document-update' });

    // Step 3: Persist to Redis as best-effort (periodic loop provides eventual consistency)
    try {
      const fullState = Y.encodeStateAsUpdate(doc);
      const persistStart = Date.now();
      await documentStore.saveDocumentState(
        documentId,
        Buffer.from(fullState),
        user.userId,
      );
      metrics.persistenceDuration.observe(
        { operation: 'save_state' },
        (Date.now() - persistStart) / 1000,
      );
      metrics.persistenceOperations.inc({
        operation: 'save_state',
        status: 'success',
      });
    } catch (err) {
      logger.error({ err, documentId, socketId: socket.id }, 'document_persist_failed');
      metrics.persistenceOperations.inc({
        operation: 'save_state',
        status: 'error',
      });
    }
  }

  private handleCursorUpdate(
    socket: Socket,
    data: {
      documentId: string;
      cursor: CursorPosition | null;
      selection: CursorPosition | null;
    },
  ): void {
    const { awareness, metrics } = this.deps;
    const updatedAwareness = awareness.updateCursor(
      socket.id,
      data.cursor,
      data.selection,
    );

    if (updatedAwareness) {
      const room = `doc:${data.documentId}`;
      socket.to(room).emit('cursor-update', {
        socketId: socket.id,
        userId: updatedAwareness.userId,
        displayName: updatedAwareness.displayName,
        color: updatedAwareness.color,
        cursor: data.cursor,
        selection: data.selection,
      });
      metrics.presenceUpdatesTotal.inc();
    }
  }

  private handleTypingIndicator(
    socket: Socket,
    data: { documentId: string; isTyping: boolean },
  ): void {
    const { awareness } = this.deps;
    const updated = awareness.setTyping(socket.id, data.isTyping);

    if (updated) {
      const room = `doc:${data.documentId}`;
      socket.to(room).emit('typing-indicator', {
        socketId: socket.id,
        userId: updated.userId,
        displayName: updated.displayName,
        isTyping: data.isTyping,
      });
    }
  }

  private handleCommentAdd(
    socket: Socket,
    data: {
      documentId: string;
      comment: Omit<CommentAnnotation, 'author' | 'createdAt'>;
    },
  ): void {
    const { metrics } = this.deps;
    const user = extractUserFromSocket(socket);
    const room = `doc:${data.documentId}`;

    const fullComment: CommentAnnotation = {
      ...data.comment,
      documentId: data.documentId,
      author: { userId: user.userId, displayName: user.displayName },
      createdAt: new Date().toISOString(),
    };

    socket.to(room).emit('comment-added', fullComment);
    socket.emit('comment-added', fullComment);
    metrics.commentAnnotationsTotal.inc({ action: 'add' });
    metrics.messagesTotal.inc({ type: 'comment-add' });
  }

  private handleCommentUpdate(
    socket: Socket,
    data: {
      documentId: string;
      commentId: string;
      content: string;
    },
  ): void {
    const { metrics } = this.deps;
    const user = extractUserFromSocket(socket);
    const room = `doc:${data.documentId}`;

    const payload = {
      commentId: data.commentId,
      content: data.content,
      updatedBy: { userId: user.userId, displayName: user.displayName },
      updatedAt: new Date().toISOString(),
    };

    socket.to(room).emit('comment-updated', payload);
    metrics.commentAnnotationsTotal.inc({ action: 'update' });
    metrics.messagesTotal.inc({ type: 'comment-update' });
  }

  private handleCommentDelete(
    socket: Socket,
    data: { documentId: string; commentId: string },
  ): void {
    const { metrics } = this.deps;
    const user = extractUserFromSocket(socket);
    const room = `doc:${data.documentId}`;

    socket.to(room).emit('comment-deleted', {
      commentId: data.commentId,
      deletedBy: user.userId,
    });
    metrics.commentAnnotationsTotal.inc({ action: 'delete' });
    metrics.messagesTotal.inc({ type: 'comment-delete' });
  }

  private async handleRequestSnapshot(
    socket: Socket,
    data: { documentId: string; label?: string },
  ): Promise<void> {
    const { documentStore, logger } = this.deps;
    const user = extractUserFromSocket(socket);
    const { documentId, label } = data;

    const doc = this.documents.get(documentId);
    if (!doc) {
      socket.emit('snapshot-error', {
        documentId,
        error: 'Document not found',
      });
      return;
    }

    try {
      const state = Y.encodeStateAsUpdate(doc);
      const snapshot = await documentStore.createSnapshot(
        documentId,
        Buffer.from(state),
        user.userId,
        label,
      );
      socket.emit('snapshot-created', snapshot);

      const room = `doc:${documentId}`;
      socket.to(room).emit('snapshot-created', snapshot);
    } catch (err) {
      logger.error({ err, documentId }, 'create_snapshot_failed');
      socket.emit('snapshot-error', {
        documentId,
        error: 'Failed to create snapshot',
      });
    }
  }

  private async handleRequestHistory(
    socket: Socket,
    data: { documentId: string; limit?: number },
  ): Promise<void> {
    const { documentStore, logger } = this.deps;
    const { documentId, limit } = data;

    try {
      const snapshots = await documentStore.getSnapshots(documentId, limit || 20);
      socket.emit('document-history', { documentId, snapshots });
    } catch (err) {
      logger.error({ err, documentId }, 'get_history_failed');
      socket.emit('history-error', {
        documentId,
        error: 'Failed to retrieve history',
      });
    }
  }

  private handleDisconnect(socket: Socket, reason: string): void {
    const { io, awareness, presenceHandler, metrics, logger } = this.deps;

    metrics.activeConnections.dec();
    logger.info({ socketId: socket.id, reason }, 'client_disconnected');

    const mapping = awareness.removeUser(socket.id);
    if (mapping) {
      const room = `doc:${mapping.documentId}`;
      socket.to(room).emit('user-left', {
        socketId: socket.id,
        userId: mapping.userId,
      });
      presenceHandler.broadcastPresenceUpdate(io, mapping.documentId);

      // Clean up empty documents
      const userCount = awareness.getDocumentUserCount(mapping.documentId);
      if (userCount === 0) {
        this.persistAndCleanupDocument(mapping.documentId);
      }
    }
  }

  async persistAndCleanupDocument(documentId: string): Promise<void> {
    // Guard against concurrent cleanup calls for the same document
    if (this.cleaningUp.has(documentId)) return;
    this.cleaningUp.add(documentId);

    const { documentStore, metrics, logger } = this.deps;
    const doc = this.documents.get(documentId);
    if (!doc) {
      this.cleaningUp.delete(documentId);
      return;
    }

    try {
      const state = Y.encodeStateAsUpdate(doc);
      await documentStore.saveDocumentState(documentId, Buffer.from(state));
      logger.info({ documentId }, 'document_persisted_on_cleanup');
      // Re-check if users have re-joined during the async persistence
      if (this.deps.awareness.getDocumentUserCount(documentId) === 0) {
        this.documents.delete(documentId);
        metrics.activeRooms.dec();
        logger.debug({ documentId }, 'document_removed_from_memory');
      }
    } catch (err) {
      logger.error({ err, documentId }, 'document_persist_on_cleanup_failed');
      // Keep document in memory so the periodic persistence loop can retry
    } finally {
      this.cleaningUp.delete(documentId);
    }
  }

  private async getOrCreateDoc(documentId: string): Promise<Y.Doc> {
    const existing = this.documents.get(documentId);
    if (existing) return existing;

    const pending = this.documentInitPromises.get(documentId);
    if (pending) return pending;

    const { documentStore, metrics } = this.deps;
    const initPromise = (async () => {
      try {
        const doc = new Y.Doc();
        const savedState = await documentStore.getDocumentState(documentId);
        if (savedState) {
          Y.applyUpdate(doc, savedState);
        }
        this.documents.set(documentId, doc);
        metrics.activeRooms.inc();
        return doc;
      } finally {
        this.documentInitPromises.delete(documentId);
      }
    })();

    this.documentInitPromises.set(documentId, initPromise);
    return initPromise;
  }

  private startPersistenceLoop(): void {
    const { documentStore, metrics, logger } = this.deps;

    this.persistTimer = setInterval(async () => {
      for (const [documentId, doc] of this.documents) {
        try {
          const state = Y.encodeStateAsUpdate(doc);
          const start = Date.now();
          await documentStore.saveDocumentState(documentId, Buffer.from(state));
          metrics.persistenceDuration.observe(
            { operation: 'periodic_save' },
            (Date.now() - start) / 1000,
          );
          metrics.persistenceOperations.inc({
            operation: 'periodic_save',
            status: 'success',
          });
        } catch (err) {
          logger.error({ err, documentId }, 'periodic_persistence_failed');
          metrics.persistenceOperations.inc({
            operation: 'periodic_save',
            status: 'error',
          });
        }
      }
    }, this.deps.persistIntervalMs);
  }

  private startSnapshotLoop(): void {
    const { documentStore, logger } = this.deps;

    this.snapshotTimer = setInterval(async () => {
      for (const [documentId, doc] of this.documents) {
        try {
          const state = Y.encodeStateAsUpdate(doc);
          await documentStore.createSnapshot(
            documentId,
            Buffer.from(state),
            'system',
            'auto-snapshot',
          );
        } catch (err) {
          logger.error({ err, documentId }, 'periodic_snapshot_failed');
        }
      }
    }, this.deps.snapshotIntervalMs);
  }
}

/** Convenience function matching the original API for backward compatibility */
export function setupCollaborationHandlers(
  io: SocketIOServer,
  documentStore: DocumentStore,
  awareness: AwarenessService,
  presenceHandler: PresenceHandler,
  metrics: MetricsCollector,
  logger: Logger,
  persistIntervalMs = 30000,
  snapshotIntervalMs = 300000,
): CollaborationManager {
  const manager = new CollaborationManager({
    io,
    documentStore,
    awareness,
    presenceHandler,
    metrics,
    logger,
    persistIntervalMs,
    snapshotIntervalMs,
  });
  manager.start();
  return manager;
}
