package com.otterworks.portal.feedback;

/** Publishes a committed feedback submission to the moderation event path. */
public interface FeedbackEventPublisher {

    void publish(Feedback feedback, String namespace);
}
