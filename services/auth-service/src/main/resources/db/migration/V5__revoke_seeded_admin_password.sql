-- Auth Service: revoke the admin password hash that used to ship in V1.
-- Databases created before this migration still carry the source-controlled hash; replace it
-- with a value BCrypt can never match. AdminUserSeeder re-seeds the password at startup from
-- AUTH_ADMIN_SEED_PASSWORD / AUTH_ADMIN_SEED_PASSWORD_HASH.
UPDATE users
SET password_hash = 'REVOKED',
    updated_at = NOW()
WHERE id = 'a0000000-0000-0000-0000-000000000001'
  AND password_hash LIKE '$2%';
