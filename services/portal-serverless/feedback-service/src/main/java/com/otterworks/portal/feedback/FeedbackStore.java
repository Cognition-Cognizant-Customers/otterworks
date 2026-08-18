package com.otterworks.portal.feedback;

import java.util.List;

/** Persistence seam for the feedback context. */
public interface FeedbackStore {

    /** Allocate the next sequential id (monolith parity: H2 identity column). */
    long nextId();

    void put(Feedback feedback);

    List<Feedback> findByUserId(String userId);

    List<Feedback> findAll();
}
