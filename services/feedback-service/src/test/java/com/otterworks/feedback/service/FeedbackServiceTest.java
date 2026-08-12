package com.otterworks.feedback.service;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.jdbc.AutoConfigureTestDatabase;
import org.springframework.boot.test.autoconfigure.orm.jpa.DataJpaTest;
import org.springframework.context.annotation.Import;
import org.springframework.test.context.ActiveProfiles;

@DataJpaTest
@AutoConfigureTestDatabase(replace = AutoConfigureTestDatabase.Replace.NONE)
@Import(FeedbackService.class)
@ActiveProfiles("test")
class FeedbackServiceTest {

  @Autowired private FeedbackService service;

  @Test
  void submitAndListForUser() {
    service.submit("u1", 5, "great");
    service.submit("u1", 3, "ok");
    service.submit("u2", 1, "bad");

    assertThat(service.listForUser("u1")).hasSize(2);
    assertThat(service.listForUser("u2")).hasSize(1);
  }

  @Test
  void averageRatingAcrossAllFeedback() {
    service.submit("u1", 4, "a");
    service.submit("u2", 2, "b");

    assertThat(service.averageRating()).isEqualTo(3.0);
  }

  @Test
  void rejectsOutOfRangeRating() {
    assertThatThrownBy(() -> service.submit("u1", 6, "too high"))
        .isInstanceOf(IllegalArgumentException.class);
    assertThatThrownBy(() -> service.submit("u1", 0, "too low"))
        .isInstanceOf(IllegalArgumentException.class);
  }
}
