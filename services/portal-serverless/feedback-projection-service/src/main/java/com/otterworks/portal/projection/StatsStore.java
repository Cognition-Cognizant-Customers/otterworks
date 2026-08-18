package com.otterworks.portal.projection;

/** Idempotent apply of one FeedbackSubmitted event to the derived stats projection. */
public interface StatsStore {

    /**
     * Applies the event exactly once: records the {@code evt#<eventId>} dedupe
     * marker and folds the rating into the running stats atomically. Returns
     * {@code true} if applied, {@code false} if the event was already processed
     * (a replayed/redelivered message is a no-op).
     */
    boolean apply(FeedbackSubmittedEvent event);
}
