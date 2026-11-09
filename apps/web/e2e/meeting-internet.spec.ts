import { test, expect, type Page } from '@playwright/test';

// Internet mode: clinician on the loopback workspace, patient on the separate public meeting-only server.
// Real WebRTC (host candidates), real data channel, synthetic browser media; ASR disabled.
const PUBLIC = process.env.INTERNET_PUBLIC_ORIGIN!;
const CHAT = 'Fictional message that must never touch the signaling server';

function watchSignaling(page: Page, frames: string[]) {
  page.on('websocket', ws => {
    if (!ws.url().includes('/signal')) return;
    ws.on('framesent', f => frames.push(String(f.payload)));
    ws.on('framereceived', f => frames.push(String(f.payload)));
  });
}

test('internet meeting: verify code, chat over the data channel, patient-device recording, end', async ({ browser, request }) => {
  const session = await (await request.post('/api/v1/sessions', { data: { session_type: 'FOLLOW_UP', consent: {} } })).json();
  const response = await request.post(`/api/v1/sessions/${session.id}/meetings`, { data: { recording_consent: true, audio_only: true } });
  expect(response.status()).toBe(201);
  const room = await response.json();
  expect(room.public_origin).toBe(PUBLIC);
  expect(room.mode).toBe('internet');

  const hostContext = await browser.newContext();
  const guestContext = await browser.newContext({ ignoreHTTPSErrors: true });
  const frames: string[] = [];
  const errors: string[] = [];
  try {
    const host = await hostContext.newPage();
    const guest = await guestContext.newPage();
    for (const page of [host, guest]) { watchSignaling(page, frames); page.on('pageerror', e => errors.push(e.message)); }
    await host.goto(`/meeting/${room.id}#token=${room.host_token}`);
    await guest.goto(`${PUBLIC}/meeting/${room.id}#token=${room.guest_token}`);

    // The public origin serves the patient page but none of the clinician workspace.
    for (const path of ['/api/v1/patients', `/api/v1/sessions/${session.id}/transcript`, '/api/v1/health', '/workspace', '/openapi.json']) {
      expect(await guest.evaluate(async p => (await fetch(p)).status, path)).toBe(404);
    }
    await expect(guest.getByText('recorded on this device', { exact: false })).toBeVisible();

    for (const page of [host, guest]) {
      await page.getByRole('checkbox').check();
      await page.getByRole('button', { name: 'Check camera & microphone' }).click();
      await page.getByRole('button', { name: 'Join meeting' }).click();
    }
    for (const page of [host, guest]) await expect(page.getByText('Connected', { exact: true })).toBeVisible({ timeout: 20000 });
    await expect(host.getByText('Audio-only meeting', { exact: false })).toBeVisible();

    // Verification code: identical on both screens; the clinician confirms it.
    const hostCode = await host.locator('.verify-code strong').textContent({ timeout: 10000 });
    const guestCode = await guest.locator('.verify-code strong').textContent({ timeout: 10000 });
    expect(hostCode).toMatch(/^\d{3} \d{3}$/);
    expect(guestCode).toBe(hostCode);
    await host.getByRole('button', { name: 'Codes match' }).click();
    await expect(guest.getByText('Your clinician confirmed the code.')).toBeVisible();

    // Chat travels on the encrypted data channel only.
    await guest.getByRole('textbox', { name: 'Chat message' }).fill(CHAT);
    await guest.getByRole('button', { name: 'Send', exact: true }).click();
    await expect(host.getByText(CHAT)).toBeVisible();

    // Recording: the patient's own device records and sends chunks over the data channel.
    await expect(guest.getByText('your voice is recorded on this device', { exact: false })).toBeVisible({ timeout: 15000 });
    await expect.poll(async () => {
      const rows = await (await request.get(`/api/v1/sessions/${session.id}/recordings`)).json();
      return rows.filter((r: any) => r.source === 'patient_device').length;
    }, { timeout: 90000, intervals: [2000] }).toBeGreaterThanOrEqual(1);
    const patient = await guest.evaluate(() => (window as any).__pshynoxMeeting.patient());
    expect(patient.clockReady).toBe(true);
    expect(patient.acked).toBeGreaterThanOrEqual(1);
    expect(Math.abs(patient.driftPpm ?? 0)).toBeLessThan(20000);   // test-only drift report (synthetic audio, loaded CI box)

    await host.getByRole('button', { name: 'End meeting', exact: true }).click();
    await expect(host.getByRole('heading', { name: 'That’s the conversation wrapped up.' })).toBeVisible({ timeout: 30000 });
    await expect(guest.getByRole('heading', { name: 'That’s the conversation wrapped up.' })).toBeVisible({ timeout: 30000 });

    const rows = await (await request.get(`/api/v1/sessions/${session.id}/recordings`)).json();
    const client = rows.filter((r: any) => r.speaker_role === 'CLIENT');
    expect(client.filter((r: any) => r.source === 'patient_device').length).toBeGreaterThanOrEqual(2);   // full minute + final partial
    expect(client.filter((r: any) => r.source === 'network_received')).toEqual([]);                      // fallback not needed
    expect(rows.filter((r: any) => r.speaker_role === 'CLINICIAN').every((r: any) => r.source === 'clinician_local')).toBe(true);
    const track = client.sort((a: any, b: any) => a.offset_ms - b.offset_ms);
    expect(track[0].duration_ms).toBe(60000);
    expect(Math.abs(track[1].offset_ms - (track[0].offset_ms + track[0].duration_ms))).toBeLessThanOrEqual(1);

    // Signaling carried connection metadata only.
    expect(frames.length).toBeGreaterThan(0);
    expect(frames.some(f => f.includes(CHAT))).toBe(false);
    expect(frames.some(f => /"type"\s*:\s*"(chat|media|recording)"/.test(f))).toBe(false);
    expect(errors).toEqual([]);
  } finally {
    await hostContext.close();
    await guestContext.close();
  }
});
