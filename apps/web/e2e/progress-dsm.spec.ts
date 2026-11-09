import { expect, test } from '@playwright/test';
import { readFileSync } from 'node:fs';

type Seed = { patient_id: string; sessions: string[]; segments: Record<string, string>; predictions: Record<string, string> };
const seed = (): Seed => JSON.parse(readFileSync(process.env.PROGRESS_DSM_SEED!, 'utf8'));

test.describe.configure({ mode: 'serial' });

test('criteria grid renders DSM items, statuses, gaps and verification badges', async ({ page }) => {
  const s = seed();
  await page.goto(`/workspace/patients/${s.patient_id}/progress`);
  await expect(page.getByRole('heading', { name: 'Criteria grid' })).toBeVisible();
  await expect(page.getByRole('button', { name: /^MDE\.A1, session 1: Present, nearly every day, AI-generated · human review pending/ })).toBeVisible();
  await expect(page.getByRole('button', { name: /^SAD\.A, session 1: Denied/ })).toBeVisible();
  await expect(page.getByRole('button', { name: /^SAD\.D, session 1: Not linked/ })).toBeVisible();   // avoidance from low energy
  await expect(page.getByRole('button', { name: /^MDE\.A1, session 2: not assessed/ })).toBeVisible(); // a gap, never "absent"
  await expect(page.getByText('Pending verification').first()).toBeVisible();
  await page.getByRole('button', { name: 'Shared findings once' }).click();
  await expect(page.getByRole('button', { name: /^Poor concentration or indecision, session 1: Present/ })).toBeVisible();
  await expect(page.getByText(/^Counts for MDE\.A8, .*GAD\.C3/)).toBeVisible();
});

test('hypothesis checklist line opens its cited turn', async ({ page }) => {
  const s = seed();
  await page.goto(`/workspace/patients/${s.patient_id}/progress`);
  const card = page.getByRole('article', { name: 'Major depressive disorder' });
  await expect(card.getByText('Criteria pattern consistent with major depressive disorder: likely')).toBeVisible();
  await expect(card.getByText('Hypothesis for clinician review; not a diagnosis.')).toBeVisible();
  const sad = page.getByRole('article', { name: 'Social anxiety disorder' });
  await expect(sad.getByText('Not met', { exact: true })).toBeVisible();
  const mde = page.getByRole('article', { name: 'Major depressive episode' });
  await page.getByText(/Episode patterns/).click();
  const line = mde.getByRole('button', { name: /^A: 6 of 9 present/ });
  await line.click();
  await mde.getByRole('link', { name: 'Open in transcript' }).first().click();
  await expect(page).toHaveURL(new RegExp(`/workspace/sessions/${s.sessions[0]}#segment-`));
});

test('finding review changes the badge and earlier tracking stays collapsed', async ({ page }) => {
  const s = seed();
  await page.goto(`/workspace/patients/${s.patient_id}/progress`);
  const earlier = page.locator('details.earlier-tracking');
  await expect(earlier.getByText('Earlier (non-DSM) tracking')).toBeVisible();
  await expect(earlier.getByRole('heading', { name: 'Symptom and functioning domains' })).toBeHidden();
  await earlier.getByText('Earlier (non-DSM) tracking').click();
  await expect(earlier.getByRole('heading', { name: 'Symptom and functioning domains' })).toBeVisible();
  await page.getByRole('button', { name: /^MDE\.A2, session 1: Present/ }).click();
  const drawer = page.getByRole('dialog', { name: /MDE\.A2/ });
  await drawer.getByPlaceholder('Reviewer name').fill('Dr E2E');
  await drawer.getByRole('button', { name: 'Accept' }).click();
  await expect(drawer.getByText('Reviewed by Dr E2E')).toBeVisible();
});

test('registry review page lists items and exports the CSV', async ({ page }) => {
  await page.goto('/workspace/registry');
  await expect(page.getByRole('heading', { name: 'DSM-5-TR criteria registry' })).toBeVisible();
  await expect(page.getByRole('row', { name: /MDE\.A4 Sleeping too little/ })).toBeVisible();
  await page.getByLabel('Filter').fill('SAD.A');
  await expect(page.getByRole('row', { name: /SAD\.A Marked fear/ })).toBeVisible();
  const download = page.waitForEvent('download');
  await page.getByRole('link', { name: 'Export CSV for review' }).click();
  expect((await download).suggestedFilename()).toBe('dsm-registry-review.csv');
});
