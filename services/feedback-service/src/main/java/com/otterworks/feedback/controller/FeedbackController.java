package com.otterworks.feedback.controller;

import com.otterworks.feedback.dto.AverageRatingResponse;
import com.otterworks.feedback.dto.FeedbackResponse;
import com.otterworks.feedback.dto.SubmitFeedbackRequest;
import com.otterworks.feedback.service.FeedbackService;
import jakarta.validation.Valid;
import java.util.List;
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
    return service.listForUser(userId).stream().map(FeedbackResponse::from).toList();
  }

  @GetMapping("/average-rating")
  public AverageRatingResponse averageRating() {
    return new AverageRatingResponse(service.averageRating());
  }
}
