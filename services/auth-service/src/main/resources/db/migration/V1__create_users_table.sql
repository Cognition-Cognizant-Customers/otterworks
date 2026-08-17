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

-- Seed admin user (password: Admin123!)
WITH admin_user AS (
    INSERT INTO users (id, email, password_hash, display_name, email_verified, created_at, updated_at)
    VALUES (
        'a0000000-0000-0000-0000-000000000001',
        'admin@otterworks.dev',
        '$2a$10$N9qo8uLOickgx2ZMRZoMyeIjZAgcfl7p92ldGxad68LJZdL17lhWy', -- nosemgrep: generic.secrets.security.detected-bcrypt-hash.detected-bcrypt-hash
        'Admin User',
        true,
        NOW(),
        NOW()
    )
    RETURNING id
)
INSERT INTO user_roles (user_id, role)
SELECT admin_user.id, admin_role.role
FROM admin_user
CROSS JOIN (VALUES ('ADMIN'), ('USER')) AS admin_role(role);
