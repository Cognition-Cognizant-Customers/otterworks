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
is created — that is the intended behaviour outside local dev, where the account should be
provisioned with a secret from the deployment's secret store.

## Local development

`docker-compose.yml` supplies the local-dev default `AUTH_ADMIN_SEED_PASSWORD=Admin123!`, so
`make up` keeps working and `admin@otterworks.dev` / `Admin123!` still logs into
<http://localhost:3000/login>. Override it by copying `.env.example` to `.env`.

The seeder is idempotent: it upserts the account on every boot, so changing the configured password
and restarting auth-service is enough to rotate it.

## Existing databases

`V5__revoke_seeded_admin_password.sql` overwrites the hash that shipped in `V1` with a value BCrypt
can never match, so databases created before this change stop accepting the leaked credential; the
seeder then sets the configured password on the next boot. Because `V1` itself changed, auth-service
runs `flyway repair` before `migrate` (see `FlywayConfig`) to realign the schema history checksum.
