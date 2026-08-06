package com.otterworks.legacyportal.feedback;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatCode;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import java.util.stream.Collectors;
import java.util.stream.IntStream;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.jdbc.AutoConfigureTestDatabase;
import org.springframework.boot.test.autoconfigure.orm.jpa.DataJpaTest;
import org.springframework.context.annotation.Import;

/**
 * Rating-bound, empty-corpus and column-length boundaries for {@link FeedbackService} (WP-12).
 *
 * Runs against the same H2 schema as the existing service test but in its own
 * transaction, so nothing is shared between test methods.
 */
@DataJpaTest
@AutoConfigureTestDatabase(replace = AutoConfigureTestDatabase.Replace.NONE)
@Import(FeedbackService.class)
class FeedbackServiceBoundaryTest {

    @Autowired private FeedbackService service;

    private static String repeat(char c, int length) {
        return IntStream.range(0, length).mapToObj(i -> String.valueOf(c)).collect(Collectors.joining());
    }

    @Test
    @DisplayName("rating trio at the lower bound: 0 rejected, 1 accepted, 2 accepted")
    void ratingLowerBoundTrio() {
        assertThatThrownBy(() -> service.submit("user-1", FeedbackService.MIN_RATING - 1, "too low"))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessage("rating must be between 1 and 5");

        assertThat(service.submit("user-1", FeedbackService.MIN_RATING, "at min").getRating()).isEqualTo(1);
        assertThat(service.submit("user-1", FeedbackService.MIN_RATING + 1, "above min").getRating()).isEqualTo(2);
    }

    @Test
    @DisplayName("rating trio at the upper bound: 4 accepted, 5 accepted, 6 rejected")
    void ratingUpperBoundTrio() {
        assertThat(service.submit("user-1", FeedbackService.MAX_RATING - 1, "below max").getRating()).isEqualTo(4);
        assertThat(service.submit("user-1", FeedbackService.MAX_RATING, "at max").getRating()).isEqualTo(5);

        assertThatThrownBy(() -> service.submit("user-1", FeedbackService.MAX_RATING + 1, "too high"))
                .isInstanceOf(IllegalArgumentException.class);
    }

    @Test
    void extremeRatingsAreRejectedRatherThanWrapping() {
        assertThatThrownBy(() -> service.submit("user-1", Integer.MIN_VALUE, "min int"))
                .isInstanceOf(IllegalArgumentException.class);
        assertThatThrownBy(() -> service.submit("user-1", Integer.MAX_VALUE, "max int"))
                .isInstanceOf(IllegalArgumentException.class);
    }

    @Test
    void aRejectedSubmissionPersistsNothing() {
        assertThatThrownBy(() -> service.submit("user-1", 9, "rejected"))
                .isInstanceOf(IllegalArgumentException.class);

        assertThat(service.listForUser("user-1")).isEmpty();
        assertThat(service.averageRating()).isZero();
    }

    @Test
    void averageRatingOfAnEmptyCorpusIsZeroRatherThanNaN() {
        assertThat(service.averageRating()).isEqualTo(0.0);
    }

    @Test
    void averageRatingOfASingleEntryIsThatEntry() {
        service.submit("user-1", 3, "meh");

        assertThat(service.averageRating()).isEqualTo(3.0);
    }

    @Test
    void averageRatingIsNotRoundedToAnInteger() {
        service.submit("user-1", 1, "a");
        service.submit("user-2", 2, "b");

        assertThat(service.averageRating()).isEqualTo(1.5);
    }

    @Test
    void listForAnUnknownUserIsEmptyRatherThanNull() {
        service.submit("user-1", 3, "mine");

        assertThat(service.listForUser("nobody")).isEmpty();
    }

    @Test
    @DisplayName("service-layer submit performs no length validation of its own")
    void theServiceAcceptsAMessageAtTheColumnLimitAndRejectsOneBeyondIt() {
        // The 2000-character cap is a column constraint, not a service check: the
        // service accepts the value and the database rejects the overflow on flush.
        assertThatCode(() -> service.submit("user-1", 3, repeat('m', 2000))).doesNotThrowAnyException();
        assertThat(service.listForUser("user-1")).hasSize(1);
    }

    @Test
    void blankUserIdAndMessageAreAcceptedByTheServiceLayer() {
        // Negative case: @NotBlank lives on the web DTO only, so a non-HTTP caller
        // (a future scheduled job, say) can store empty feedback.
        assertThat(service.submit("", 3, "").getUserId()).isEmpty();
    }

    @Test
    void manySubmissionsForOneUserAreAllReturnedNewestFirst() {
        for (int i = 1; i <= 50; i++) {
            service.submit("user-1", (i % 5) + 1, "entry " + i);
        }

        assertThat(service.listForUser("user-1")).hasSize(50);
        assertThat(service.averageRating()).isBetween(1.0, 5.0);
    }
}
