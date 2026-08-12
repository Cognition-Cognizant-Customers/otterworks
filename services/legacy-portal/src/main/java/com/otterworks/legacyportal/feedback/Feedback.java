package com.otterworks.legacyportal.feedback;

import java.time.Instant;
import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.GeneratedValue;
import jakarta.persistence.GenerationType;
import jakarta.persistence.Id;
import jakarta.persistence.Table;

/**
 * Bounded context: feedback. Owns the {@code feedback} schema.
 */
@Entity
@Table(name = "feedback", schema = "feedback")
public class Feedback {

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;

    @Column(name = "user_id", nullable = false, length = 100)
    private String userId;

    @Column(nullable = false)
    private int rating;

    @Column(nullable = false, length = 2000)
    private String message;

    @Column(nullable = false, updatable = false)
    private Instant createdAt;

    protected Feedback() {
        // JPA
    }

    public Feedback(String userId, int rating, String message) {
        this.userId = userId;
        this.rating = rating;
        this.message = message;
        this.createdAt = Instant.now();
    }

    public Long getId() {
        return id;
    }

    public String getUserId() {
        return userId;
    }

    public int getRating() {
        return rating;
    }

    public String getMessage() {
        return message;
    }

    public Instant getCreatedAt() {
        return createdAt;
    }
}
