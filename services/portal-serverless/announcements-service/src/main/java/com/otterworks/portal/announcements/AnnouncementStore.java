package com.otterworks.portal.announcements;

import java.util.List;
import java.util.Optional;

/** Persistence seam for the announcements context (DynamoDB in production, in-memory in tests). */
public interface AnnouncementStore {

    /** Allocate the next sequential id (monolith parity: H2 identity column). */
    long nextId();

    void put(Announcement announcement);

    Optional<Announcement> find(long id);

    List<Announcement> findAll();
}
