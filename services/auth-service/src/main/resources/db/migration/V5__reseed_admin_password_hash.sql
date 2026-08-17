-- Rotate the seeded admin account off the password digest that used to be committed in V1.
-- Databases created before that digest was removed still hold it, so the seeded fixture
-- account is re-hashed here from the same placeholder V1 uses; databases created from the
-- current V1 simply get another freshly salted digest of the same password. Only the seeded
-- fixture row is touched. As in V1, the placeholder is substituted textually and must not
-- contain a single quote.
DO $reseed$
BEGIN
    IF '${seedAdminPassword}' <> '' THEN
        EXECUTE 'CREATE EXTENSION IF NOT EXISTS pgcrypto';

        UPDATE users
        SET password_hash = crypt('${seedAdminPassword}', gen_salt('bf', 10)),
            updated_at = NOW()
        WHERE id = 'a0000000-0000-0000-0000-000000000001';
    END IF;
END
$reseed$;
