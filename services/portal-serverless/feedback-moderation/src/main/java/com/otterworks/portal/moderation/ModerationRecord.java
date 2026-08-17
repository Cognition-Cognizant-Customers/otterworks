package com.otterworks.portal.moderation;

import java.time.Instant;

public record ModerationRecord(
        String idempotencyKey,
        long feedbackId,
        String userId,
        String status,
        String reason,
        Instant reviewedAt) {}
