package com.otterworks.portal.preferences;

import java.util.Optional;

/** Persistence seam for the user-preferences context. */
public interface PreferenceStore {

    Optional<UserPreference> find(String userId);

    void put(UserPreference preference);
}
