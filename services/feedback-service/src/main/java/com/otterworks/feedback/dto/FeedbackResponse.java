package com.otterworks.feedback.dto;

import com.otterworks.feedback.entity.Feedback;
import java.time.Instant;

public class FeedbackResponse {

  private final Long id;
  private final String userId;
  private final int rating;
  private final String message;
  private final Instant createdAt;

  private FeedbackResponse(Long id, String userId, int rating, String message, Instant createdAt) {
    this.id = id;
    this.userId = userId;
    this.rating = rating;
    this.message = message;
    this.createdAt = createdAt;
  }

  public static FeedbackResponse from(Feedback f) {
    return new FeedbackResponse(
        f.getId(), f.getUserId(), f.getRating(), f.getMessage(), f.getCreatedAt());
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
