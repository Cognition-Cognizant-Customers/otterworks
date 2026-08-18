package com.otterworks.portal.feedback;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.util.UUID;

/** Identity and shape of the FeedbackSubmitted domain event. */
public final class FeedbackEvents {

    public static final String SOURCE = "otterworks.portal.feedback";
    public static final String DETAIL_TYPE = "FeedbackSubmitted";

    /** UUIDv5 namespace for feedback event identity (fixed for the estate). */
    static final UUID NAMESPACE = UUID.fromString("d9b2d63d-a233-5fc1-9f3d-6a1e1f0f7a5e");

    private FeedbackEvents() {}

    /**
     * Deterministic event id: UUIDv5 (SHA-1, name-based) over "feedback:&lt;id&gt;".
     * The same committed feedback row always yields the same event id, so any
     * redelivery or replay is deduplicatable by the consumer.
     */
    public static UUID eventId(long feedbackId) {
        byte[] name = ("feedback:" + feedbackId).getBytes(StandardCharsets.UTF_8);
        MessageDigest sha1;
        try {
            sha1 = MessageDigest.getInstance("SHA-1");
        } catch (NoSuchAlgorithmException e) {
            throw new IllegalStateException(e);
        }
        sha1.update(toBytes(NAMESPACE));
        byte[] hash = sha1.digest(name);
        hash[6] &= 0x0f;
        hash[6] |= 0x50; // version 5
        hash[8] &= 0x3f;
        hash[8] |= (byte) 0x80; // IETF variant
        long msb = 0;
        long lsb = 0;
        for (int i = 0; i < 8; i++) {
            msb = (msb << 8) | (hash[i] & 0xff);
        }
        for (int i = 8; i < 16; i++) {
            lsb = (lsb << 8) | (hash[i] & 0xff);
        }
        return new UUID(msb, lsb);
    }

    private static byte[] toBytes(UUID uuid) {
        byte[] out = new byte[16];
        long msb = uuid.getMostSignificantBits();
        long lsb = uuid.getLeastSignificantBits();
        for (int i = 0; i < 8; i++) {
            out[i] = (byte) (msb >>> (8 * (7 - i)));
            out[8 + i] = (byte) (lsb >>> (8 * (7 - i)));
        }
        return out;
    }
}
