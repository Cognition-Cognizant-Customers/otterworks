export const BILLING_BASE_URL =
  import.meta.env.VITE_BILLING_SERVICE_URL ||
  `${globalThis.location.origin}/billing-api`;

export class BillingApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
  }
}

export type Plan = {
  plan_id: string;
  code: string;
  tier: string;
  monthly_fee: string;
  included_units: number;
  overage_rate: string;
};

export type Entitlement = {
  tenant_id: string;
  plan_code: string;
  tier: string;
  monthly_fee: string;
  included_units: number;
  subscription_status: string;
  effective_on: string;
};

export type Subscription = {
  plan_id: string;
  starts_on: string;
  ends_on: string | null;
  status: string;
};

export type PlanChangeResult = {
  latest_plan: string;
  latest_start: string;
  subscriptions: Subscription[];
};

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const response = await fetch(`${BILLING_BASE_URL}${path}`, options);
  if (!response.ok) {
    throw new BillingApiError(
      `Billing service returned ${response.status}`,
      response.status,
    );
  }
  return response.json() as Promise<T>;
}

export const billingApi = {
  listPlans: () => request<Plan[]>("/api/plans"),
  entitlement: (tenantId: string, on: string) =>
    request<Entitlement>(
      `/api/tenants/${encodeURIComponent(tenantId)}/entitlement?on=${encodeURIComponent(on)}`
    ),
  changePlan: (tenantId: string, planId: string, effectiveOn: string) =>
    request<PlanChangeResult>(
      `/api/tenants/${encodeURIComponent(tenantId)}/plan-change`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ plan_id: planId, effective_on: effectiveOn }),
      }
    ),
};
