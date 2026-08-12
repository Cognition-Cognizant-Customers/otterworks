package com.otterworks.feedback.repository;

import com.otterworks.feedback.entity.Feedback;
import java.util.List;
import org.springframework.data.jpa.repository.JpaRepository;

public interface FeedbackRepository extends JpaRepository<Feedback, Long> {

  List<Feedback> findByUserIdOrderByCreatedAtDesc(String userId);
}
