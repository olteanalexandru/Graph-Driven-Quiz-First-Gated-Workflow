import { existsSync } from 'node:fs';
import { defineConfig, devices } from '@playwright/test';

// Base URL: local production build by default; set BASE_URL to test a Vercel preview.
// Previews need the Protection Bypass for Automation secret in VERCEL_AUTOMATION_BYPASS_SECRET.
const bypass = process.env.VERCEL_AUTOMATION_BYPASS_SECRET;

// Signed-in tests: add tests/auth.setup.ts that signs in once and saves
// .auth/user.json; every project below then starts signed in.
const hasAuth = existsSync('tests/auth.setup.ts');
const signedIn = hasAuth ? { storageState: '.auth/user.json' } : {};
const needsAuth = hasAuth ? { dependencies: ['setup'] } : {};

export default defineConfig({
  testDir: 'tests',
  fullyParallel: true,
  retries: process.env.CI ? 2 : 0,
  // The line reporter keeps output (and agent tokens) small; CI merges blob reports.
  reporter: process.env.CI ? [['blob'], ['github']] : 'line',
  use: {
    baseURL: process.env.BASE_URL ?? 'http://localhost:3000',
    trace: 'on-first-retry',
    extraHTTPHeaders: bypass ? { 'x-vercel-protection-bypass': bypass, 'x-vercel-set-bypass-cookie': 'true' } : {},
  },
  webServer: process.env.BASE_URL
    ? undefined
    : {
        command: 'npm run build && npm run start',
        env: { NEXT_PUBLIC_E2E: '1' },
        url: 'http://localhost:3000',
        reuseExistingServer: !process.env.CI,
        timeout: 180_000,
      },
  projects: [
    ...(hasAuth ? [{ name: 'setup', testMatch: /auth\.setup\.ts/ }] : []),
    {
      name: 'chromium',
      ...needsAuth,
      use: { ...devices['Desktop Chrome'], ...signedIn },
      grepInvert: /@audio-|@visual|@ro\b/,
    },
    {
      // Tests tagged @ro run with a Romanian locale; rename for your second language.
      name: 'ro',
      ...needsAuth,
      use: { ...devices['Desktop Chrome'], locale: 'ro-RO', ...signedIn },
      grep: /@ro\b/,
    },
    {
      name: 'visual',
      use: { ...devices['Desktop Chrome'], viewport: { width: 1280, height: 800 } },
      grep: /@visual/,
    },
    // Microphone tests: Chromium can play a WAV file as the microphone. The file is fixed
    // per browser, so give each audio fixture its own project and tag its tests, e.g.:
    // {
    //   name: 'audio-m3',
    //   ...needsAuth,
    //   grep: /@audio-m3/,
    //   use: {
    //     ...devices['Desktop Chrome'], ...signedIn, permissions: ['microphone'],
    //     launchOptions: { args: [
    //       '--use-fake-ui-for-media-stream', '--use-fake-device-for-media-stream',
    //       `--use-file-for-fake-audio-capture=${process.cwd()}/tests/e2e/audio/C4-E4.wav`,
    //     ] },
    //   },
    // },
  ],
});
