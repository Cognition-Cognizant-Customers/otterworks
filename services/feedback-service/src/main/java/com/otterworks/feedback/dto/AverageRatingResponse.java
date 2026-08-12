package com.otterworks.feedback.dto;

public class AverageRatingResponse {

  private final double averageRating;

  public AverageRatingResponse(double averageRating) {
    this.averageRating = averageRating;
  }

  public double getAverageRating() {
    return averageRating;
  }
}
