package com.otterworks.report;

import io.jsonwebtoken.Jwts;
import io.jsonwebtoken.security.Keys;

import java.nio.charset.StandardCharsets;
import java.util.Arrays;

public final class TestAuth {

    public static final String TEST_SECRET =
            "test-jwt-secret-otterworks-must-be-at-least-32-bytes-long-for-hmac";

    private TestAuth() {
    }

    public static String tokenFor(String userId) {
        return Jwts.builder()
                .subject(userId)
                .claim("roles", Arrays.asList("USER"))
                .signWith(Keys.hmacShaKeyFor(TEST_SECRET.getBytes(StandardCharsets.UTF_8)),
                        Jwts.SIG.HS256)
                .compact();
    }
}
