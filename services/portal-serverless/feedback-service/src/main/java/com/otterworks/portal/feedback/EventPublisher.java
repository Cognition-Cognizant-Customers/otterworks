package com.otterworks.portal.feedback;

/** Publishes the FeedbackSubmitted domain event after the write commits. */
public interface EventPublisher {

    /** No-op publisher for estates without an event bus (unit tests, plain fixture). */
    EventPublisher NONE = feedback -> {};

    void publishSubmitted(Feedback feedback);
}
