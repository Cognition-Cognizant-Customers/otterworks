import * as Y from 'yjs';

export const TEXT_FIELD = 'content';

/** A client-side Yjs replica that records every update it produces, base64-encoded. */
export interface Replica {
  doc: Y.Doc;
  text(): Y.Text;
  read(): string;
  /** Base64 updates emitted by local edits since the last drain. */
  drainUpdates(): string[];
  /** Base64 update of the single most recent local edit. */
  lastUpdate(): string;
  applyB64(update: string): void;
  stateB64(): string;
  stateVectorB64(): string;
  /** Diff of this replica against another replica's state vector. */
  diffSinceB64(other: Replica): string;
}

export interface ReplicaOptions {
  /** Base64 state to seed the replica with (e.g. the server's sync-document payload). */
  state?: string;
  /** Fixed Yjs client id — makes concurrent-edit tie-breaking deterministic. */
  clientId?: number;
}

export function createReplica(options: ReplicaOptions = {}): Replica {
  const doc = new Y.Doc();
  if (options.clientId !== undefined) doc.clientID = options.clientId;

  const pending: string[] = [];

  if (options.state) {
    Y.applyUpdate(doc, new Uint8Array(Buffer.from(options.state, 'base64')), 'remote');
  }

  doc.on('update', (update: Uint8Array, origin: unknown) => {
    if (origin === 'remote') return;
    pending.push(Buffer.from(update).toString('base64'));
  });

  const replica: Replica = {
    doc,
    text: () => doc.getText(TEXT_FIELD),
    read: () => doc.getText(TEXT_FIELD).toString(),
    drainUpdates: () => pending.splice(0, pending.length),
    lastUpdate: () => {
      const updates = pending.splice(0, pending.length);
      if (updates.length === 0) throw new Error('replica produced no local update');
      return updates[updates.length - 1];
    },
    applyB64: (update: string) => {
      Y.applyUpdate(doc, new Uint8Array(Buffer.from(update, 'base64')), 'remote');
    },
    stateB64: () => Buffer.from(Y.encodeStateAsUpdate(doc)).toString('base64'),
    stateVectorB64: () => Buffer.from(Y.encodeStateVector(doc)).toString('base64'),
    diffSinceB64: (other: Replica) =>
      Buffer.from(Y.encodeStateAsUpdate(doc, Y.encodeStateVector(other.doc))).toString(
        'base64',
      ),
  };

  return replica;
}

export function readDoc(doc: Y.Doc): string {
  return doc.getText(TEXT_FIELD).toString();
}

/** A syntactically valid base64 string that is not a decodable Yjs update. */
export function garbageUpdateB64(byteLength = 64): string {
  return Buffer.alloc(byteLength, 0xff).toString('base64');
}

/** The 2-byte update produced by an untouched document — valid but empty. */
export function emptyUpdateB64(): string {
  return Buffer.from(Y.encodeStateAsUpdate(new Y.Doc())).toString('base64');
}
