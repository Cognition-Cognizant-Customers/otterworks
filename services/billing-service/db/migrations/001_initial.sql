CREATE SCHEMA IF NOT EXISTS billing_svc;

CREATE TABLE IF NOT EXISTS billing_svc.tenants (
    id uuid PRIMARY KEY,
    name text NOT NULL UNIQUE,
    tax_exempt boolean NOT NULL DEFAULT false,
    status text NOT NULL CHECK (status IN ('active', 'suspended'))
);

CREATE TABLE IF NOT EXISTS billing_svc.plans (
    id uuid PRIMARY KEY,
    code text NOT NULL UNIQUE,
    tier text NOT NULL CHECK (tier IN ('starter', 'growth', 'scale')),
    monthly_fee numeric(12, 2) NOT NULL CHECK (monthly_fee >= 0),
    included_units integer NOT NULL CHECK (included_units >= 0),
    overage_rate numeric(12, 6) NOT NULL CHECK (overage_rate >= 0),
    active boolean NOT NULL DEFAULT true
);

CREATE TABLE IF NOT EXISTS billing_svc.subscriptions (
    id uuid PRIMARY KEY,
    tenant_id uuid NOT NULL REFERENCES billing_svc.tenants(id),
    plan_id uuid NOT NULL REFERENCES billing_svc.plans(id),
    starts_on date NOT NULL,
    ends_on date,
    status text NOT NULL CHECK (status IN ('active', 'suspended', 'cancelled')),
    suspended_on date,
    CHECK (ends_on IS NULL OR ends_on >= starts_on),
    CHECK (suspended_on IS NULL OR suspended_on >= starts_on)
);

CREATE TABLE IF NOT EXISTS billing_svc.usage_events (
    id uuid PRIMARY KEY,
    tenant_id uuid NOT NULL REFERENCES billing_svc.tenants(id),
    occurred_at timestamptz NOT NULL,
    units integer NOT NULL CHECK (units > 0),
    kind text NOT NULL CHECK (kind IN ('api', 'storage', 'compute'))
);

CREATE TABLE IF NOT EXISTS billing_svc.rating_periods (
    id uuid PRIMARY KEY,
    tenant_id uuid NOT NULL REFERENCES billing_svc.tenants(id),
    period_start date NOT NULL,
    period_end date NOT NULL,
    UNIQUE (tenant_id, period_start),
    CHECK (period_end >= period_start)
);

CREATE TABLE IF NOT EXISTS billing_svc.rating_results (
    id uuid PRIMARY KEY,
    period_id uuid NOT NULL REFERENCES billing_svc.rating_periods(id),
    subscription_id uuid NOT NULL REFERENCES billing_svc.subscriptions(id),
    used_units integer NOT NULL CHECK (used_units >= 0),
    quota_units integer NOT NULL CHECK (quota_units >= 0),
    rollover_units integer NOT NULL CHECK (rollover_units >= 0),
    billable_units integer NOT NULL CHECK (billable_units >= 0),
    overage_amount numeric(12, 2) NOT NULL CHECK (overage_amount >= 0),
    created_at timestamptz NOT NULL
);
