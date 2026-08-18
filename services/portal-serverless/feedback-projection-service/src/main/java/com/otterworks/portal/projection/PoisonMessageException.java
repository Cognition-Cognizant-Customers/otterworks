package com.otterworks.portal.projection;

/**
 * Non-retryable message per the unit contract's malformed-record policy:
 * malformed JSON, missing/empty eventId, feedbackId or userId, or rating
 * outside 1-5. Reported as a batch item failure on every receive until the
 * redrive policy delivers the message to the DLQ with its full payload.
 */
public class PoisonMessageException extends RuntimeException {

    public PoisonMessageException(String message) {
        super(message);
    }

    public PoisonMessageException(String message, Throwable cause) {
        super(message, cause);
    }
}
