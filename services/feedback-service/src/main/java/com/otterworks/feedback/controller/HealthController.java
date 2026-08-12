package com.otterworks.feedback.controller;

import java.util.LinkedHashMap;
import java.util.Map;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RestController;

/**
 * Lightweight health endpoint matching the {@code /health} convention used by the other OtterWorks
 * services.
 */
@RestController
public class HealthController {

  @GetMapping("/health")
  public Map<String, String> health() {
    Map<String, String> body = new LinkedHashMap<>();
    body.put("status", "UP");
    body.put("service", "feedback-service");
    return body;
  }
}
