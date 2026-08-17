-- Seed (or re-seed) the admin user from the credentials supplied at migration
-- time by SeedAdminConfig: the hash is derived from SEED_ADMIN_PASSWORD, and
-- when that variable is unset the placeholder is empty and nothing is seeded.
-- Databases created before this migration carry the previously hard-coded
-- hash; the upsert below replaces it.
INSERT INTO users (id, email, password_hash, display_name, email_verified, created_at, updated_at)
SELECT
    'a0000000-0000-0000-0000-000000000001'::uuid,
    '${seed_admin_email}',
    '${seed_admin_password_hash}',
    'Admin User',
    true,
    NOW(),
    NOW()
WHERE '${seed_admin_password_hash}' <> ''
ON CONFLICT (id) DO UPDATE
SET email = EXCLUDED.email,
    password_hash = EXCLUDED.password_hash,
    updated_at = NOW();

INSERT INTO user_roles (user_id, role)
SELECT 'a0000000-0000-0000-0000-000000000001'::uuid, role
FROM (VALUES ('ADMIN'), ('USER')) AS seeded_roles(role)
WHERE '${seed_admin_password_hash}' <> ''
ON CONFLICT DO NOTHING;
