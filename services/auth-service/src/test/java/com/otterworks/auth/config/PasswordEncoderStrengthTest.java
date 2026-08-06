package com.otterworks.auth.config;

import static org.assertj.core.api.Assertions.*;

import org.junit.jupiter.api.MethodOrderer;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.TestMethodOrder;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.security.crypto.password.PasswordEncoder;
import org.springframework.test.context.ActiveProfiles;

/**
 * Pins the BCrypt work factor used for stored password hashes. A silent downgrade of the cost
 * factor (or a switch to a weaker encoder) must fail the build rather than ship quietly.
 */
@SpringBootTest
@ActiveProfiles("test")
@TestMethodOrder(MethodOrderer.Random.class)
class PasswordEncoderStrengthTest {

  @Autowired private PasswordEncoder passwordEncoder;

  @Test
  void passwordEncoder_encode_producesBcryptHashWithCostFactor12() {
    String hash = passwordEncoder.encode("password123");

    assertThat(hash).startsWith("$2a$12$");
  }

  @Test
  void passwordEncoder_encode_producesDistinctHashesForTheSamePassword() {
    assertThat(passwordEncoder.encode("password123"))
        .isNotEqualTo(passwordEncoder.encode("password123"));
  }

  @Test
  void passwordEncoder_matches_acceptsCorrectPasswordAndRejectsWrongOne() {
    String hash = passwordEncoder.encode("password123");

    assertThat(passwordEncoder.matches("password123", hash)).isTrue();
    assertThat(passwordEncoder.matches("password124", hash)).isFalse();
  }
}
