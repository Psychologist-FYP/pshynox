import { test, expect } from '@playwright/test';

test('source context, query errors, and edited evidence in the session UI', async ({ page, request }) => {
  const session = await (await request.post('/api/v1/sessions', {
    data: { session_type: 'FOLLOW_UP', consent: {} },
  })).json();
  const clinician = { id: 'source-clinician', session_id: session.id, start_ms: 0, end_ms: 900,
    speaker_role: 'CLINICIAN', text: 'Have you been sleeping less?', revision: 1 };
  let client = { id: 'source-client', session_id: session.id, start_ms: 1000, end_ms: 1900,
    speaker_role: 'CLIENT', text: 'No. I sleep four hours because of night shifts.', revision: 1 };
  // Backend semantics are covered by API tests. Here fixture responses exercise
  // the browser's rendering, cancellation/error state and source-edit refresh.
  await page.route(`**/api/v1/sessions/${session.id}/transcript`, route => route.fulfill({ json: [clinician, client] }));
  await page.route(`**/api/v1/sessions/${session.id}/transcript/source-client`, async route => {
    client = { ...client, ...route.request().postDataJSON(), revision: client.revision + 1 };
    await route.fulfill({ json: client });
  });
  await page.route(`**/api/v1/sessions/${session.id}/evidence?**`, async route => {
    const query = new URL(route.request().url()).searchParams.get('query') || '';
    if (query === '"sleep') {
      await route.fulfill({ status: 422, json: { error: { code: 'INVALID_SEARCH_QUERY', message: 'Close the quoted phrase.' } } });
      return;
    }
    const matches = query.includes('exercise') ? client.text.includes('exercise') : client.text.includes('sleep');
    await route.fulfill({ json: matches ? [{ segment_id: client.id, session_id: session.id,
      start_ms: client.start_ms, end_ms: client.end_ms, excerpt: client.text, score: 1.2,
      section: 'subjective', query, speaker_role: client.speaker_role,
      sources: [{ ...clinician, matched: false }, { ...client, matched: true }] }] : [] });
  });
  await page.goto(`/workspace/sessions/${session.id}`);
  const evidence = page.locator('.evidence-panel');
  await expect(evidence.getByText(client.text, { exact: true })).toBeVisible();
  await evidence.getByText('Surrounding conversation').click();
  await expect(evidence.locator('details p').filter({ hasText: clinician.text })).toBeVisible();
  await expect(evidence.getByText(/Context · CLINICIAN/)).toBeVisible();

  await page.getByLabel('Search this session').fill('"sleep');
  await expect(evidence.getByRole('alert')).toHaveText('Close the quoted phrase.');
  await expect(page.locator('#segment-source-client').getByText(client.text, { exact: true })).toBeVisible();
  await page.getByLabel('Search this session').fill('sleep');
  await expect(evidence.getByText(client.text, { exact: true })).toBeVisible();
  const original = client.text;
  const row = page.locator('#segment-source-client');
  await row.getByRole('button', { name: 'Edit segment' }).click();
  await row.getByRole('textbox').fill('I now enjoy exercise.');
  await row.getByRole('button', { name: 'Save edit' }).click();
  await expect(row.getByText('I now enjoy exercise.', { exact: true })).toBeVisible();
  await expect(evidence.getByText('No matching excerpts.', { exact: false })).toBeVisible();
  await expect(evidence.getByText(original, { exact: true })).toHaveCount(0);
  await page.getByLabel('Search this session').fill('exercise');
  await expect(evidence.getByText('I now enjoy exercise.', { exact: true })).toBeVisible();
});
