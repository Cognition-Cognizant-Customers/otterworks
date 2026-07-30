function parseVersion(version: string): [number, number, number] {
  const match = /(\d+)\.(\d+)\.(\d+)/.exec(version);
  if (!match) {
    throw new Error(`Unparseable version: ${version}`);
  }
  return [Number(match[1]), Number(match[2]), Number(match[3])];
}

function gte(a: string, b: string): boolean {
  const [aMaj, aMin, aPat] = parseVersion(a);
  const [bMaj, bMin, bPat] = parseVersion(b);
  if (aMaj !== bMaj) return aMaj > bMaj;
  if (aMin !== bMin) return aMin > bMin;
  return aPat >= bPat;
}

describe('dependency security guards', () => {
  it('resolves lodash to a version patched for CVE-2020-28500, CVE-2021-23337, and CVE-2026-4800 (>=4.18.0)', () => {
    // eslint-disable-next-line @typescript-eslint/no-var-requires
    const { version } = require('lodash/package.json') as { version: string };
    expect(gte(version, '4.18.0')).toBe(true);
  });

  it('declares a lodash range in package.json whose floor is >=4.18.0', () => {
    // eslint-disable-next-line @typescript-eslint/no-var-requires
    const pkg = require('../../package.json') as {
      dependencies: Record<string, string>;
    };
    const range = pkg.dependencies['lodash'];
    expect(range).toBeDefined();
    expect(gte(range, '4.18.0')).toBe(true);
  });
});
