import { defineConfig } from '@playwright/test';
import { mkdtempSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { resolve } from 'node:path';
import { execFileSync } from 'node:child_process';

// Fictional seed data only; a temporary database that is never the developer's records.
// The config is evaluated in every Playwright process; seed once and share the directory.
const root = resolve('../..');
const data = process.env.PROGRESS_E2E_DIR ?? mkdtempSync(`${tmpdir()}/pshynox-progress-ui-`);
if (!process.env.PROGRESS_E2E_DIR) {
  const env = { ...process.env, DATABASE_URL: `sqlite:///${data}/test.db`, LOCAL_DATA_DIR: data, SPEECH_WORKER_ENABLED: 'false', FIXTURE_MODE: 'false', APP_ENV: 'test', PROGRESS_REGISTRY: 'v1' };
  execFileSync(`${root}/.venv/bin/python`, ['-m', 'tests.progress.e2e_seed', `${data}/seed.json`], { cwd: root, env });
  process.env.PROGRESS_E2E_DIR = data;
}
process.env.PROGRESS_SEED = `${data}/seed.json`;

export default defineConfig({
  testDir: './e2e', testMatch: 'progress.spec.ts', workers: 1, timeout: 60000,
  use: { baseURL: 'http://127.0.0.1:5182', channel: 'chrome', trace: 'retain-on-failure' },
  webServer: [
    { cwd: root, command: '.venv/bin/python -m uvicorn services.api.main:app --host 127.0.0.1 --port 8014',
      env: { DATABASE_URL: `sqlite:///${data}/test.db`, LOCAL_DATA_DIR: data, SPEECH_WORKER_ENABLED: 'false', FIXTURE_MODE: 'false', PROGRESS_REGISTRY: 'v1' },
      url: 'http://127.0.0.1:8014/api/v1/health' },
    { command: 'npm run dev -- --host 127.0.0.1 --port 5182 --strictPort', env: { VITE_API_TARGET: 'http://127.0.0.1:8014' }, url: 'http://127.0.0.1:5182' },
  ],
});
