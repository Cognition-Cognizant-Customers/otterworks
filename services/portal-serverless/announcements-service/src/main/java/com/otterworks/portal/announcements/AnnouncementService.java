package com.otterworks.portal.announcements;

import com.otterworks.portal.common.ApiException;
import java.time.Instant;
import java.util.Comparator;
import java.util.List;
import java.util.stream.Collectors;

/** Business rules mirroring the monolith's AnnouncementService. */
public class AnnouncementService {

    private final AnnouncementStore store;

    public AnnouncementService(AnnouncementStore store) {
        this.store = store;
    }

    public Announcement create(String title, String body, boolean published) {
        Announcement announcement =
                new Announcement(store.nextId(), title, body, published, Instant.now());
        store.put(announcement);
        return announcement;
    }

    public Announcement get(long id) {
        return store.find(id)
                .orElseThrow(() -> ApiException.notFound("announcement " + id + " not found"));
    }

    public Announcement publish(long id) {
        Announcement announcement = get(id);
        announcement.setPublished(true);
        store.put(announcement);
        return announcement;
    }

    /** Published only, newest first (monolith: findByPublishedTrueOrderByCreatedAtDesc). */
    public List<Announcement> listPublished() {
        return store.findAll().stream()
                .filter(Announcement::isPublished)
                .sorted(Comparator.comparing(Announcement::getCreatedAt)
                        .thenComparing(Announcement::getId)
                        .reversed())
                .collect(Collectors.toList());
    }

    /** All announcements in insertion order (monolith: findAll on the identity PK). */
    public List<Announcement> listAll() {
        return store.findAll().stream()
                .sorted(Comparator.comparing(Announcement::getId))
                .collect(Collectors.toList());
    }
}
