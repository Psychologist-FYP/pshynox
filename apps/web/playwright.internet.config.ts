import { defineConfig } from '@playwright/test';
import { mkdtempSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { resolve } from 'node:path';
import { execFileSync } from 'node:child_process';

// Internet-mode meeting test: clinician UI and API on loopback; the patient uses the separate public meeting server
// (HTTPS with a local test certificate standing in for Tailscale Funnel's TLS). No TURN provider is contacted
// (TURN_PROVIDER=none; mocked STUN entry). Temporary database and audio folder; the speech worker is off.
const root = resolve('../..');
const data = process.env.INTERNET_E2E_DIR ?? mkdtempSync(`${tmpdir()}/pshynox-internet-e2e-`);
if (!process.env.INTERNET_E2E_DIR) {
  execFileSync(`${root}/.venv/bin/python`, ['-c', 'from scripts.run_lan import certificates; from pathlib import Path; import sys; certificates(Path(sys.argv[1]), "127.0.0.1")', data], { cwd: root });
  execFileSync('npm', ['run', 'build:meeting', '--silent'], { cwd: resolve('.'), stdio: 'inherit' });
  process.env.INTERNET_E2E_DIR = data;
}
const PUBLIC = 'https://127.0.0.1:8461';
process.env.INTERNET_PUBLIC_ORIGIN = PUBLIC;
const env = { DATABASE_URL: `sqlite:///${data}/test.db`, LOCAL_DATA_DIR: data, SPEECH_WORKER_ENABLED: 'false', FIXTURE_MODE: 'false', APP_ENV: 'e2e',
  MEETING_MODE: 'internet', MEETING_PUBLIC_ORIGIN: PUBLIC, TURN_PROVIDER: 'none', MEETING_ICE_SERVERS: '[{"urls":["stun:127.0.0.1:3478"]}]', MEETING_LOG_LEVEL: 'warning' };

export default defineConfig({
  testDir: './e2e', testMatch: 'meeting-internet.spec.ts', workers: 1, timeout: 150000,
  use: { baseURL: 'http://127.0.0.1:5189', channel: 'chrome', ignoreHTTPSErrors: true, permissions: ['camera', 'microphone'],
    launchOptions: { args: ['--use-fake-ui-for-media-stream', '--use-fake-device-for-media-stream', '--autoplay-policy=no-user-gesture-required'] },
    trace: 'retain-on-failure' },
  webServer: [
    { cwd: root, env, url: 'http://127.0.0.1:8031/api/v1/health',
      command: `.venv/bin/python -m services.meeting_public.serve --api-port 8031 --public-port 8461 --public-ssl-certfile ${data}/meeting.pem --public-ssl-keyfile ${data}/meeting-key.pem` },
    { command: 'npm run dev -- --host 127.0.0.1 --port 5189 --strictPort', env: { VITE_API_TARGET: 'http://127.0.0.1:8031' }, url: 'http://127.0.0.1:5189' },
  ],
});
