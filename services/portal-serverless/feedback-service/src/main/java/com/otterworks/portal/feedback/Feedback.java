package com.otterworks.portal.feedback;

import java.time.Instant;

/** Mirrors the monolith's Feedback entity/JSON shape. */
public class Feedback {

    private Long id;
    private String userId;
    private int rating;
    private String message;
    private Instant createdAt;

    public Feedback() {}

    public Feedback(Long id, String userId, int rating, String message, Instant createdAt) {
        this.id = id;
        this.userId = userId;
        this.rating = rating;
        this.message = message;
        this.createdAt = createdAt;
    }

    public Long getId() {
        return id;
    }

    public void setId(Long id) {
        this.id = id;
    }

    public String getUserId() {
        return userId;
    }

    public void setUserId(String userId) {
        this.userId = userId;
    }

    public int getRating() {
        return rating;
    }

    public void setRating(int rating) {
        this.rating = rating;
    }

    public String getMessage() {
        return message;
    }

    public void setMessage(String message) {
        this.message = message;
    }

    public Instant getCreatedAt() {
        return createdAt;
    }

    public void setCreatedAt(Instant createdAt) {
        this.createdAt = createdAt;
    }
}
