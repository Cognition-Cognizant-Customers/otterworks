package com.otterworks.feedback.service;

import com.otterworks.feedback.entity.Feedback;
import com.otterworks.feedback.repository.FeedbackRepository;
import java.util.List;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

@Service
public class FeedbackService {

  static final int MIN_RATING = 1;
  static final int MAX_RATING = 5;

  private final FeedbackRepository repository;

  public FeedbackService(FeedbackRepository repository) {
    this.repository = repository;
  }

  @Transactional
  public Feedback submit(String userId, int rating, String message) {
    if (rating < MIN_RATING || rating > MAX_RATING) {
      throw new IllegalArgumentException(
          "rating must be between " + MIN_RATING + " and " + MAX_RATING);
    }
    return repository.save(new Feedback(userId, rating, message));
  }

  @Transactional(readOnly = true)
  public List<Feedback> listForUser(String userId) {
    return repository.findByUserIdOrderByCreatedAtDesc(userId);
  }

  @Transactional(readOnly = true)
  public double averageRating() {
    List<Feedback> all = repository.findAll();
    if (all.isEmpty()) {
      return 0.0;
    }
    return all.stream().mapToInt(Feedback::getRating).average().orElse(0.0);
  }
}
