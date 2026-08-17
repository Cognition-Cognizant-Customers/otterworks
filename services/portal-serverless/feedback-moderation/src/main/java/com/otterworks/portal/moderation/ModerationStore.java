package com.otterworks.portal.moderation;

public interface ModerationStore {

    boolean putIfAbsent(ModerationRecord record);
}
