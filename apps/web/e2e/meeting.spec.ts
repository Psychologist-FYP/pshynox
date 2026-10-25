import { test, expect, type Page } from '@playwright/test';

async function expectRemoteVideo(page: Page) {
  const video = page.locator('.call-stage .call-tile:not(.self-tile) video');
  await expect(video).not.toHaveClass(/camera-hidden/);
  await expect.poll(() => video.evaluate(element => {
    const player = element as HTMLVideoElement;
    return player.videoWidth > 0 ? player.getVideoPlaybackQuality().totalVideoFrames : 0;
  }), { timeout: 15000 }).toBeGreaterThan(0);
  const frames = await video.evaluate(element => (element as HTMLVideoElement).getVideoPlaybackQuality().totalVideoFrames);
  await expect.poll(() => video.evaluate(element => (element as HTMLVideoElement).getVideoPlaybackQuality().totalVideoFrames)).toBeGreaterThan(frames);
}

for (const cameras of [
  { host: false, guest: true },
  { host: true, guest: false },
  { host: false, guest: false },
]) {
  test(`independent cameras: clinician starts ${cameras.host ? 'on' : 'off'}, patient starts ${cameras.guest ? 'on' : 'off'}`, async ({ browser, request }) => {
    const session = await (await request.post('/api/v1/sessions', { data: { session_type: 'FOLLOW_UP', consent: {} } })).json();
    const response = await request.post(`/api/v1/sessions/${session.id}/meetings`, { data: { recording_consent: true } });
    expect(response.status()).toBe(201);
    const room = await response.json();
    const hostContext = await browser.newContext({ permissions: ['camera', 'microphone'], ignoreHTTPSErrors: true });
    const guestContext = await browser.newContext({ permissions: ['camera', 'microphone'], ignoreHTTPSErrors: true });
    try {
      const host = await hostContext.newPage();
      const guest = await guestContext.newPage();
      await host.goto(`/meeting/${room.id}#token=${room.host_token}`);
      await guest.goto(`${process.env.MEETING_TEST_GUEST_ORIGIN}/meeting/${room.id}#token=${room.guest_token}`);
      for (const [page, camera] of [[host, cameras.host], [guest, cameras.guest]] as const) {
        await page.getByRole('checkbox').check();
        if (!camera) await page.getByRole('button', { name: 'Turn camera off / audio only', exact: true }).click();
        await page.getByRole('button', { name: 'Check camera & microphone' }).click();
        await page.getByRole('button', { name: 'Join meeting' }).click();
      }
      for (const page of [host, guest]) {
        await expect(page.getByText('Connected', { exact: true })).toBeVisible({ timeout: 20000 });
        await expect(page.getByText('Audio recording · separate speaker tracks')).toBeVisible();
      }
      if (cameras.guest) await expectRemoteVideo(host);
      if (cameras.host) await expectRemoteVideo(guest);
      // Enable a camera first acquired after joining, then toggle each independently.
      for (const [sender, receiver, initiallyOn] of [[host, guest, cameras.host], [guest, host, cameras.guest]] as const) {
        const audioTrackId = await sender.locator('.self-tile video').evaluate(element =>
          ((element as HTMLVideoElement).srcObject as MediaStream).getAudioTracks()[0].id);
        if (!initiallyOn) await sender.getByRole('button', { name: 'Turn camera on', exact: true }).click();
        await expectRemoteVideo(receiver);
        await sender.getByRole('button', { name: 'Turn camera off', exact: true }).click();
        await expect(receiver.locator('.call-stage .call-tile:not(.self-tile) video')).toHaveClass(/camera-hidden/);
        await sender.getByRole('button', { name: 'Turn camera on', exact: true }).click();
        await expectRemoteVideo(receiver);
        expect(await sender.locator('.self-tile video').evaluate(element =>
          ((element as HTMLVideoElement).srcObject as MediaStream).getAudioTracks()[0].id)).toBe(audioTrackId);
      }
      await host.getByRole('button', { name: 'End meeting', exact: true }).click();
      await expect(host.getByRole('heading', { name: 'That’s the conversation wrapped up.' })).toBeVisible();
      await expect(guest.getByRole('heading', { name: 'That’s the conversation wrapped up.' })).toBeVisible();
    } finally {
      await hostContext.close();
      await guestContext.close();
    }
  });
}

// Real WebRTC + signaling + database; synthetic browser cameras/microphones.
// ASR is disabled so this checks recording/queuing independently of model speed.
test('two participants connect, chat, mute, save minute chunks, and end cleanly', async ({ browser, request }) => {
  const created = await request.post('/api/v1/sessions', { data: { session_type: 'FOLLOW_UP', consent: {} } });
  const session = await created.json();
  const roomResponse = await request.post(`/api/v1/sessions/${session.id}/meetings`, { data: { recording_consent: true } });
  const room = await roomResponse.json();
  expect(roomResponse.status()).toBe(201);
  const hostContext = await browser.newContext({ permissions: ['camera', 'microphone'], ignoreHTTPSErrors: true });
  const guestContext = await browser.newContext({ permissions: ['camera', 'microphone'], ignoreHTTPSErrors: true });
  const host = await hostContext.newPage();
  const guest = await guestContext.newPage();
  const errors: string[] = [];
  host.on('pageerror', e => errors.push(e.message));
  guest.on('pageerror', e => errors.push(e.message));
  await host.goto(`https://127.0.0.1:5179/meeting/${room.id}#token=${room.host_token}`);
  await guest.goto(`${process.env.MEETING_TEST_GUEST_ORIGIN}/meeting/${room.id}#token=${room.guest_token}`);
  // LAN invitees may join the call but cannot browse the clinician's records.
  if (!process.env.MEETING_TEST_GUEST_ORIGIN?.includes('127.0.0.')) {
    expect(await guest.evaluate(async () => (await fetch('/api/v1/patients')).status)).toBe(403);
  }
  for (const page of [host, guest]) {
    await page.getByRole('checkbox').check();
    await page.getByRole('button', { name: 'Check camera & microphone' }).click();
    await expect(page.getByRole('button', { name: 'Join meeting' })).toBeEnabled();
    await page.getByRole('button', { name: 'Join meeting' }).click();
  }
  await expect(host.getByText('Connected', { exact: true })).toBeVisible({ timeout: 20000 });
  await expect(guest.getByText('Connected', { exact: true })).toBeVisible({ timeout: 20000 });
  await expect(host.getByText('Audio recording · separate speaker tracks')).toBeVisible();
  await expect(guest.getByText('Audio recording · separate speaker tracks')).toBeVisible();
  await host.screenshot({ path: 'test-results/meeting-connected.png', fullPage: true });
  await guest.getByRole('textbox', { name: 'Chat message' }).fill('Hello from the fictional patient');
  await guest.getByRole('button', { name: 'Send', exact: true }).click();
  await expect(host.getByText('Hello from the fictional patient')).toBeVisible();
  await guest.getByRole('button', { name: 'Mute microphone', exact: true }).click();
  await expect(host.getByText('The other participant’s microphone is muted')).toBeVisible();
  await guest.getByRole('button', { name: 'Unmute microphone' }).click();
  // Wait for actual audio rendering, not a mocked wall-clock timer.
  await expect.poll(async () => {
    const r = await request.get(`/api/v1/sessions/${session.id}/recordings`);
    return (await r.json()).length;
  }, { timeout: 80000, intervals: [2000] }).toBeGreaterThanOrEqual(2);
  await host.getByRole('button', { name: 'End meeting', exact: true }).click();
  await expect(host.getByRole('heading', { name: 'That’s the conversation wrapped up.' })).toBeVisible({ timeout: 20000 });
  await expect(guest.getByRole('heading', { name: 'That’s the conversation wrapped up.' })).toBeVisible({ timeout: 20000 });
  const recordings = await (await request.get(`/api/v1/sessions/${session.id}/recordings`)).json();
  expect(recordings.filter((r: any) => r.speaker_role === 'CLIENT').length).toBeGreaterThanOrEqual(2);
  expect(recordings.filter((r: any) => r.speaker_role === 'CLINICIAN').length).toBeGreaterThanOrEqual(2);
  for (const role of ['CLIENT', 'CLINICIAN']) {
    const track = recordings.filter((r: any) => r.speaker_role === role).sort((a: any, b: any) => a.offset_ms - b.offset_ms);
    expect(track[0].duration_ms).toBe(60000);
    expect(Math.abs(track[1].offset_ms - (track[0].offset_ms + track[0].duration_ms))).toBeLessThanOrEqual(1);
  }
  expect(errors).toEqual([]);
  await hostContext.close(); await guestContext.close();
});
