// Mints the local-dev admin JWT consumed by the mocked login (see DevAuthConfigService).
// Runs before `npm start` / `npm run build` so the token is never committed; signed with
// the same JWT_SECRET default the backend services use in docker-compose.yml.
import { createHmac } from 'node:crypto';
import { mkdirSync, writeFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const SECRET = process.env.JWT_SECRET || 'otterworks-local-dev-jwt-secret-change-me-in-production';
const USER_ID = 'a0000000-0000-0000-0000-000000000001';
const TTL_SECONDS = 12 * 60 * 60;

const base64Url = input => Buffer.from(input).toString('base64url');

const now = Math.floor(Date.now() / 1000);
const signingInput = [
  base64Url(JSON.stringify({ alg: 'HS256', typ: 'JWT' })),
  base64Url(
    JSON.stringify({
      sub: USER_ID,
      user_id: USER_ID,
      email: 'admin@otterworks.dev',
      role: 'admin',
      iat: now,
      exp: now + TTL_SECONDS,
    })
  ),
].join('.');
const signature = createHmac('sha256', SECRET).update(signingInput).digest('base64url');

const outputPath = join(dirname(fileURLToPath(import.meta.url)), '..', 'src', 'assets', 'dev-auth.json');
mkdirSync(dirname(outputPath), { recursive: true });
writeFileSync(outputPath, `${JSON.stringify({ token: `${signingInput}.${signature}` }, null, 2)}\n`);
