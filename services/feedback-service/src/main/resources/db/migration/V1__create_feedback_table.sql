-- Feedback bounded context, extracted from the legacy-portal monolith.
-- This service owns the feedback schema exclusively; no other service or the
-- monolith may reference these tables.
CREATE TABLE IF NOT EXISTS feedback (
    id BIGSERIAL PRIMARY KEY,
    user_id VARCHAR(100) NOT NULL,
    rating INTEGER NOT NULL CHECK (rating BETWEEN 1 AND 5),
    message VARCHAR(2000) NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_feedback_user_id_created_at
    ON feedback (user_id, created_at DESC);
