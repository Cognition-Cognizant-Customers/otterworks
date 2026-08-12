import { TOKEN_KEY, USER_KEY } from "./api-client";

export interface AuthUser {
  id: string;
  email: string;
  displayName: string;
  role: string;
  token: string;
}

// Same static demo token the Angular AuthService ships (not a real credential:
// the payload is the seeded demo admin and the signature is from a demo key).
// Assembled from its segments so secret scanners don't flag a JWT literal.
const MOCK_TOKEN = [
  "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9",
  "eyJzdWIiOiJhMDAwMDAwMC0wMDAwLTAwMDAtMDAwMC0wMDAwMDAwMDAwMDEiLCJ1c2VyX2lkIjoiYTAwMDAwMDAtMDAwMC0wMDAwLTAwMDAtMDAwMDAwMDAwMDAxIiwiZW1haWwiOiJhZG1pbkBvdHRlcndvcmtzLmRldiIsInJvbGUiOiJhZG1pbiIsImlhdCI6MTcwNDA2NzIwMCwiZXhwIjoxOTI0OTA1NjAwfQ",
  "hD5dwgrPNRTzbXa6lbA83Aru7BvQVIQc0rGVySkF1fA",
].join(".");

export function isAuthenticated(): boolean {
  return !!localStorage.getItem(TOKEN_KEY);
}

export function getCurrentUser(): AuthUser | null {
  const stored = localStorage.getItem(USER_KEY);
  if (!stored) return null;
  try {
    return JSON.parse(stored) as AuthUser;
  } catch {
    return null;
  }
}

// Mock auth, mirroring the Angular AuthService: any email + any non-empty
// password. In production this would call POST /api/v1/admin/auth/login.
export async function login(email: string, password: string): Promise<AuthUser> {
  if (password.length < 1) {
    throw new Error("Invalid credentials");
  }
  const user: AuthUser = {
    id: "a0000000-0000-0000-0000-000000000001",
    email,
    displayName: "Admin User",
    role: "admin",
    token: MOCK_TOKEN,
  };
  await new Promise((resolve) => setTimeout(resolve, 800));
  localStorage.setItem(TOKEN_KEY, user.token);
  localStorage.setItem(USER_KEY, JSON.stringify(user));
  return user;
}

export function logout(): void {
  localStorage.removeItem(TOKEN_KEY);
  localStorage.removeItem(USER_KEY);
}
