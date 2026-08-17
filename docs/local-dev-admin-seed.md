# Admin user seeding (auth-service)

The admin account (`a0000000-0000-0000-0000-000000000001`) used by the web app login flow and by
`scripts/seed.py` is no longer created by a Flyway migration — a password hash in source control is
a leaked credential the moment the repo is cloned. `AdminUserSeeder` provisions it at auth-service
startup from configuration instead.

## Configuration

| Environment variable              | Default                | Purpose                                             |
| --------------------------------- | ---------------------- | --------------------------------------------------- |
| `AUTH_ADMIN_SEED_ENABLED`         | `true`                 | Set to `false` to disable seeding entirely.          |
| `AUTH_ADMIN_SEED_EMAIL`           | `admin@otterworks.dev` | Email of the seeded account.                         |
| `AUTH_ADMIN_SEED_DISPLAY_NAME`    | `Admin User`           | Display name of the seeded account.                  |
| `AUTH_ADMIN_SEED_PASSWORD`        | _(unset)_              | Plain-text password, BCrypt-hashed before storage.   |
| `AUTH_ADMIN_SEED_PASSWORD_HASH`   | _(unset)_              | Pre-computed BCrypt hash; wins over the plain value. |

If neither the password nor the hash is set, seeding is skipped with a warning and no admin account
is created. Seeding is also skipped (with a warning, not a crash) when the configured email already
belongs to a different account.

## Cluster deployments

`scripts/deploy-dev.sh` and `scripts/deploy-tenant.sh` pass `AUTH_ADMIN_SEED_PASSWORD` to the
auth-service release as a Helm secret. They default to the same documented demo credential as local
dev so deployed environments keep a usable admin login; export `ADMIN_SEED_PASSWORD` (and optionally
`ADMIN_SEED_EMAIL`) for anything that is not a demo.

## Local development

`docker-compose.yml` supplies the local-dev default `AUTH_ADMIN_SEED_PASSWORD=Admin123!`, so
`make up` keeps working and `admin@otterworks.dev` / `Admin123!` still logs into
<http://localhost:3000/login>. Override it by copying `.env.example` to `.env`.

The seeder is idempotent: it upserts the account on every boot, but it only writes the password when
the account is created or its stored hash is missing/`REVOKED`, so a password changed through
`/change-password` survives restarts. Set `AUTH_ADMIN_SEED_FORCE_PASSWORD_RESET=true` for one boot to
reset the account to the configured password.

## Existing databases

`V5__revoke_seeded_admin_password.sql` overwrites the hash that shipped in `V1` with a value BCrypt
can never match, so databases created before this change stop accepting the leaked credential; the
seeder then sets the configured password on the next boot. The revocation matches the leaked hash by
MD5 digest, so an admin password that was already rotated is left alone.

Because `V1` itself changed, a database migrated by the previous `V1` records the old checksum.
`FlywayConfig` validates before migrating and runs `flyway repair` only when the sole validation
error is a checksum mismatch on `V1`, so that upgrade heals itself once and any other drift — an
edited or missing migration — still fails startup.
