-- Revoke the admin password digest that used to be committed in V1. Databases created before that
-- digest was removed still hold it, so it is rotated here. The row is matched by an MD5 fingerprint
-- of the compromised digest rather than the digest itself, so nothing usable is stored in the tree
-- and an admin password that was changed since seeding is never clobbered. With a seed password
-- configured the account is re-hashed from the same placeholder V1 uses; with seeding disabled the
-- digest is replaced by an unusable value so the published credential stops working either way.
-- Fresh databases created from the current V1 never match, making this a no-op there. As in V1, the
-- placeholder is substituted textually and must not contain a single quote (the service refuses to
-- start otherwise).
DO $reseed$
DECLARE
    leaked_digest_fingerprint CONSTANT text := 'a015f44e73c0c6c1714cb3a335fc6988';
BEGIN
    EXECUTE 'CREATE EXTENSION IF NOT EXISTS pgcrypto';

    IF '${seedAdminPassword}' <> '' THEN
        UPDATE users
        SET password_hash = crypt('${seedAdminPassword}', gen_salt('bf', 10)),
            updated_at = NOW()
        WHERE md5(password_hash) = leaked_digest_fingerprint;
    ELSE
        UPDATE users
        SET password_hash = '!disabled',
            updated_at = NOW()
        WHERE md5(password_hash) = leaked_digest_fingerprint;
    END IF;
END
$reseed$;
