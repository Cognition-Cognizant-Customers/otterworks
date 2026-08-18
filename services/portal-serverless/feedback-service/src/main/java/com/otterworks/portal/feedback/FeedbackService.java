package com.otterworks.portal.feedback;

import com.otterworks.portal.common.ApiException;
import java.time.Instant;
import java.util.Comparator;
import java.util.List;
import java.util.stream.Collectors;

/** Business rules mirroring the monolith's FeedbackService. */
public class FeedbackService {

    static final int MIN_RATING = 1;
    static final int MAX_RATING = 5;

    private final FeedbackStore store;
    private final EventPublisher publisher;

    public FeedbackService(FeedbackStore store) {
        this(store, EventPublisher.NONE);
    }

    public FeedbackService(FeedbackStore store, EventPublisher publisher) {
        this.store = store;
        this.publisher = publisher;
    }

    public Feedback submit(String userId, int rating, String message) {
        if (rating < MIN_RATING || rating > MAX_RATING) {
            throw ApiException.badRequest(
                    "rating must be between " + MIN_RATING + " and " + MAX_RATING);
        }
        Feedback feedback = new Feedback(store.nextId(), userId, rating, message, Instant.now());
        store.put(feedback);
        try {
            publisher.publishSubmitted(feedback);
        } catch (RuntimeException e) {
            // Write-then-publish: a publish failure never fails the committed request;
            // the async recon's events-published==submissions check detects the gap.
            System.err.println("feedback event publish failed for id "
                    + feedback.getId() + ": " + e);
        }
        return feedback;
    }

    /** Newest first (monolith: findByUserIdOrderByCreatedAtDesc). */
    public List<Feedback> listForUser(String userId) {
        return store.findByUserId(userId).stream()
                .sorted(Comparator.comparing(Feedback::getCreatedAt)
                        .thenComparing(Feedback::getId)
                        .reversed())
                .collect(Collectors.toList());
    }

    public double averageRating() {
        List<Feedback> all = store.findAll();
        if (all.isEmpty()) {
            return 0.0;
        }
        return all.stream().mapToInt(Feedback::getRating).average().orElse(0.0);
    }
}
