-- Auth Service: User management schema
CREATE TABLE IF NOT EXISTS users (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email VARCHAR(255) NOT NULL UNIQUE,
    password_hash VARCHAR(255) NOT NULL,
    display_name VARCHAR(100) NOT NULL,
    avatar_url VARCHAR(500),
    email_verified BOOLEAN NOT NULL DEFAULT false,
    mfa_enabled BOOLEAN NOT NULL DEFAULT false,
    mfa_secret VARCHAR(255),
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    last_login_at TIMESTAMP WITH TIME ZONE
);

CREATE TABLE IF NOT EXISTS user_roles (
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    role VARCHAR(50) NOT NULL,
    PRIMARY KEY (user_id, role)
);

CREATE INDEX idx_users_email ON users(email);
CREATE INDEX idx_users_created_at ON users(created_at);

-- Seed admin user. The password comes from the Flyway placeholder `seedAdminPassword`
-- (env AUTH_SEED_ADMIN_PASSWORD) and is hashed here with a freshly salted bcrypt
-- digest, so no password digest is stored in the source tree. When the placeholder is
-- empty no admin user is seeded at all. Flyway substitutes the value textually into the
-- quoted literals below, so it must not contain a single quote.
DO $seed$
BEGIN
    IF '${seedAdminPassword}' <> '' THEN
        EXECUTE 'CREATE EXTENSION IF NOT EXISTS pgcrypto';

        INSERT INTO users (id, email, password_hash, display_name, email_verified, created_at, updated_at)
        VALUES (
            'a0000000-0000-0000-0000-000000000001',
            'admin@otterworks.dev',
            crypt('${seedAdminPassword}', gen_salt('bf', 10)),
            'Admin User',
            true,
            NOW(),
            NOW()
        );

        INSERT INTO user_roles (user_id, role) VALUES
        ('a0000000-0000-0000-0000-000000000001', 'ADMIN'),
        ('a0000000-0000-0000-0000-000000000001', 'USER');
    END IF;
END
$seed$;
