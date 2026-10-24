import { defineConfig } from '@playwright/test';
import { mkdtempSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { resolve } from 'node:path';
import { execFileSync } from 'node:child_process';

const data = mkdtempSync(`${tmpdir()}/pshynox-call-e2e-`);
const root = resolve('../..');
let lanIP = process.env.MEETING_TEST_LAN_IP || '127.0.0.2';
if (!process.env.MEETING_TEST_LAN_IP) {
  try { lanIP = JSON.parse(execFileSync('ip', ['-j', 'route', 'get', '1.1.1.1'], { encoding: 'utf8' }))[0].prefsrc || lanIP; } catch { /* loopback alias fallback */ }
}
execFileSync(`${root}/.venv/bin/python`, ['-c', 'from scripts.run_lan import certificates; from pathlib import Path; import sys; certificates(Path(sys.argv[1]), sys.argv[2])', data, lanIP], { cwd: root });
process.env.MEETING_TEST_GUEST_ORIGIN = `https://${lanIP}:5179`;
console.log(`Meeting browser test: clinician localhost, patient ${process.env.MEETING_TEST_GUEST_ORIGIN}`);
export default defineConfig({
  testDir: './e2e', timeout: 120000, workers: 1,
  use: {
    baseURL: 'https://127.0.0.1:5179', channel: 'chrome', ignoreHTTPSErrors: true,
    permissions: ['camera', 'microphone'],
    launchOptions: { args: ['--use-fake-ui-for-media-stream', '--use-fake-device-for-media-stream', '--autoplay-policy=no-user-gesture-required'] },
    trace: 'retain-on-failure',
  },
  webServer: [
    { command: `${root}/.venv/bin/python -m uvicorn services.api.main:app --host 127.0.0.1 --port 8011`, cwd: root,
      env: { DATABASE_URL: `sqlite:///${data}/test.db`, LOCAL_DATA_DIR: data, FIXTURE_MODE: 'false', SPEECH_WORKER_ENABLED: 'false', MEETING_PUBLIC_ORIGIN: `https://${lanIP}:5179` },
      url: 'http://127.0.0.1:8011/api/v1/health', reuseExistingServer: false },
    { command: 'npm run dev -- --host 0.0.0.0 --port 5179 --strictPort',
      env: { VITE_API_TARGET: 'http://127.0.0.1:8011', VITE_HTTPS_CERT: `${data}/meeting.pem`, VITE_HTTPS_KEY: `${data}/meeting-key.pem` },
      url: 'https://127.0.0.1:5179', ignoreHTTPSErrors: true, reuseExistingServer: false },
  ],
});
