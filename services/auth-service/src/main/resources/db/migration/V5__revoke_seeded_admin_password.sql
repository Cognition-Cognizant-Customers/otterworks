-- Auth Service: revoke the admin password hash that used to ship in V1.
-- Databases created before this migration still carry the source-controlled hash; replace it
-- with a value BCrypt can never match. AdminUserSeeder re-seeds the password at startup from
-- AUTH_ADMIN_SEED_PASSWORD / AUTH_ADMIN_SEED_PASSWORD_HASH.
-- Matched by digest so the leaked hash is not re-introduced into source control, and so an
-- already-rotated admin password is left untouched.
UPDATE users
SET password_hash = 'REVOKED',
    updated_at = NOW()
WHERE id = 'a0000000-0000-0000-0000-000000000001'
  AND md5(password_hash) = 'a015f44e73c0c6c1714cb3a335fc6988';
