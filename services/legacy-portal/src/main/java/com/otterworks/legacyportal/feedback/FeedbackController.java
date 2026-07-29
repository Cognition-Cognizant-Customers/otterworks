package com.otterworks.legacyportal.feedback;

import java.time.Instant;
import java.util.List;
import java.util.stream.Collectors;
import jakarta.validation.Valid;
import jakarta.validation.constraints.Max;
import jakarta.validation.constraints.Min;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.Size;
import org.springframework.http.HttpStatus;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.ResponseStatus;
import org.springframework.web.bind.annotation.RestController;

@RestController
@RequestMapping("/api/feedback")
public class FeedbackController {

    private final FeedbackService service;

    public FeedbackController(FeedbackService service) {
        this.service = service;
    }

    @PostMapping
    @ResponseStatus(HttpStatus.CREATED)
    public FeedbackResponse submit(@Valid @RequestBody SubmitFeedbackRequest request) {
        return FeedbackResponse.from(
                service.submit(request.getUserId(), request.getRating(), request.getMessage()));
    }

    @GetMapping
    public List<FeedbackResponse> listForUser(@RequestParam String userId) {
        return service.listForUser(userId).stream()
                .map(FeedbackResponse::from)
                .collect(Collectors.toList());
    }

    @GetMapping("/average-rating")
    public AverageRatingResponse averageRating() {
        return new AverageRatingResponse(service.averageRating());
    }

    public static class SubmitFeedbackRequest {

        @NotBlank
        @Size(max = 100)
        private String userId;

        @Min(1)
        @Max(5)
        private int rating;

        @NotBlank
        @Size(max = 2000)
        private String message;

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
    }

    public static class FeedbackResponse {

        private final Long id;
        private final String userId;
        private final int rating;
        private final String message;
        private final Instant createdAt;

        private FeedbackResponse(
                Long id, String userId, int rating, String message, Instant createdAt) {
            this.id = id;
            this.userId = userId;
            this.rating = rating;
            this.message = message;
            this.createdAt = createdAt;
        }

        static FeedbackResponse from(Feedback f) {
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

    public static class AverageRatingResponse {

        private final double averageRating;

        AverageRatingResponse(double averageRating) {
            this.averageRating = averageRating;
        }

        public double getAverageRating() {
            return averageRating;
        }
    }
}
