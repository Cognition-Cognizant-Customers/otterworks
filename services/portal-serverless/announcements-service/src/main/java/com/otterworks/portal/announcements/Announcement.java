package com.otterworks.portal.announcements;

import java.time.Instant;

/** Mirrors the monolith's Announcement entity/JSON shape. */
public class Announcement {

    private Long id;
    private String title;
    private String body;
    private boolean published;
    private Instant createdAt;

    public Announcement() {}

    public Announcement(Long id, String title, String body, boolean published, Instant createdAt) {
        this.id = id;
        this.title = title;
        this.body = body;
        this.published = published;
        this.createdAt = createdAt;
    }

    public Long getId() {
        return id;
    }

    public void setId(Long id) {
        this.id = id;
    }

    public String getTitle() {
        return title;
    }

    public void setTitle(String title) {
        this.title = title;
    }

    public String getBody() {
        return body;
    }

    public void setBody(String body) {
        this.body = body;
    }

    public boolean isPublished() {
        return published;
    }

    public void setPublished(boolean published) {
        this.published = published;
    }

    public Instant getCreatedAt() {
        return createdAt;
    }

    public void setCreatedAt(Instant createdAt) {
        this.createdAt = createdAt;
    }
}
