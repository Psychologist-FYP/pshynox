import {defineConfig} from '@playwright/test';
import {mkdtempSync} from 'node:fs';
import {tmpdir} from 'node:os';
const data=mkdtempSync(`${tmpdir()}/pshynox-notes-ui-`);
export default defineConfig({testDir:'./e2e',testMatch:'notes.spec.ts',workers:1,use:{baseURL:'http://127.0.0.1:5181',channel:'chrome'},webServer:[
{cwd:'../..',command:'.venv/bin/python -m uvicorn services.api.main:app --host 127.0.0.1 --port 8013',env:{DATABASE_URL:`sqlite:///${data}/test.db`,LOCAL_DATA_DIR:data,SPEECH_WORKER_ENABLED:'false'},url:'http://127.0.0.1:8013/api/v1/health'},
{command:'npm run dev -- --host 127.0.0.1 --port 5181',env:{VITE_API_TARGET:'http://127.0.0.1:8013'},url:'http://127.0.0.1:5181'}]});
