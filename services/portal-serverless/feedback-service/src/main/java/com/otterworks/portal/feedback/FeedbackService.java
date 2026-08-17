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

    public FeedbackService(FeedbackStore store) {
        this.store = store;
    }

    public Feedback submit(String userId, int rating, String message) {
        if (rating < MIN_RATING || rating > MAX_RATING) {
            throw ApiException.badRequest(
                    "rating must be between " + MIN_RATING + " and " + MAX_RATING);
        }
        Feedback feedback = new Feedback(store.nextId(), userId, rating, message, Instant.now());
        store.put(feedback);
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
