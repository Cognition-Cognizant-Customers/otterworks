-- One schema per bounded context. These are the decomposition seams: each schema can
-- become an independent database when legacy-portal is split into microservices.
CREATE SCHEMA IF NOT EXISTS announcements;
CREATE SCHEMA IF NOT EXISTS user_preferences;
