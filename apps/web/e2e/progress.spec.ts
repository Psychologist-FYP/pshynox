import { expect, test } from '@playwright/test';
import { readFileSync } from 'node:fs';

type Seed = { patient_id: string; sessions: string[]; segments: Record<string, string>; sleep_observation: string; prediction: string; risk: string };
const seed = (): Seed => JSON.parse(readFileSync(process.env.PROGRESS_SEED!, 'utf8'));

test.describe.configure({ mode: 'serial' });

test('progress page renders AI values with pending badges, gaps and empty-state wording', async ({ page }) => {
  const s = seed();
  await page.goto(`/workspace/patients/${s.patient_id}/progress`);
  await expect(page.getByRole('heading', { name: 'Fictional E2E Patient' })).toBeVisible();
  await expect(page.getByText('3 sessions')).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Clinical hypotheses' })).toBeVisible();
  await expect(page.getByRole('heading', { name: 'PHQ-9' })).toBeVisible();
  await expect(page.getByText('AI-generated · human review pending').first()).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Symptom and functioning domains' })).toBeVisible();
  // Table fallback shows the AI estimate and an explicit gap for the unassessed session.
  await page.getByRole('region', { name: 'PHQ-9' }).getByRole('button', { name: 'Show table' }).click();
  const table = page.getByRole('region', { name: 'PHQ-9' }).locator('table');
  await expect(table.getByRole('row', { name: /S2/ })).toContainText('8');
  await expect(table.getByRole('row', { name: /S3/ })).toContainText('gap');
});

test('hypothesis card expands and a citation opens the transcript turn', async ({ page }) => {
  const s = seed();
  await page.goto(`/workspace/patients/${s.patient_id}/progress`);
  const card = page.getByRole('article', { name: 'Depressive episode (depression)' });
  await card.getByRole('button', { name: /Why\? Show criteria/ }).click();
  await expect(card.getByText('✓ Met')).toBeVisible();
  await expect(card.getByText('Session 1 · 1:05–1:06')).toBeVisible();
  await card.getByRole('link', { name: 'Open in transcript' }).first().click();
  await expect(page).toHaveURL(new RegExp(`/workspace/sessions/${s.sessions[0]}#segment-${s.segments.low}`));
  await expect(page.locator(`#segment-${s.segments.low}`)).toHaveClass(/source-focused/);
  await expect(page.locator(`#segment-${s.segments.low}`)).toBeInViewport();
});

test('review badges change after accept, edit and reject', async ({ page }) => {
  const s = seed();
  await page.goto(`/workspace/patients/${s.patient_id}/progress`);
  const card = page.getByRole('article', { name: 'Depressive episode (depression)' });
  await card.getByPlaceholder('Reviewer name').fill('Dr E2E');
  await card.getByRole('button', { name: 'Accept' }).click();
  await expect(page.getByRole('article', { name: 'Depressive episode (depression)' }).getByText('Reviewed by Dr E2E')).toBeVisible();

  await page.getByRole('button', { name: /^Sleep, session 2/ }).click();
  const drawer = page.getByRole('dialog', { name: 'Sleep evidence' });
  await drawer.getByPlaceholder('Reviewer name').fill('Dr E2E');
  await drawer.getByRole('button', { name: 'Edit' }).click();
  await drawer.getByLabel('Rating').selectOption('1');
  await drawer.getByRole('button', { name: 'Save edit' }).click();
  await expect(drawer.getByText('Edited by Dr E2E')).toBeVisible();
  await expect(drawer.getByText('Original AI output retained')).toBeVisible();

  const risk = page.getByRole('region', { name: /Flagged statements/ });
  await risk.getByPlaceholder('Reviewer name').fill('Dr E2E');
  await risk.getByRole('button', { name: 'Reject' }).click();
  await risk.getByLabel('Reason').fill('Fictional statement was about a film plot');
  await risk.getByRole('button', { name: 'Confirm rejection' }).click();
  await expect(risk.getByText('No statements flagged by the model. This is not a risk assessment.')).toBeVisible();
  await expect(risk.getByText('Rejected flags kept in history (1)')).toBeVisible();
});

test('add a PHQ-9 administration and acknowledge a change', async ({ page }) => {
  const s = seed();
  await page.goto(`/workspace/patients/${s.patient_id}/progress`);
  const phq = page.getByRole('region', { name: 'PHQ-9' });
  await phq.getByRole('button', { name: 'Add administration' }).click();
  const form = phq.getByRole('form', { name: 'Add PHQ-9 administration' });
  await form.getByLabel('Clinician').fill('Dr E2E');
  await form.getByLabel('Session').selectOption(s.sessions[2]);
  for (let i = 1; i <= 9; i++) await form.getByLabel(`Item ${i}`, { exact: true }).selectOption('1');
  await form.getByRole('button', { name: 'Save administration' }).click();
  await phq.getByRole('button', { name: 'Show table' }).click();
  await expect(phq.locator('table').getByRole('row', { name: /S3/ })).toContainText('Entered by Dr E2E');

  const changes = page.getByRole('region', { name: 'Changes between sessions' });
  await expect(changes.getByText('AI values (review pending)').first()).toBeVisible();
  await changes.getByPlaceholder('Your name').fill('Dr E2E');
  await changes.getByRole('button', { name: 'Acknowledge' }).first().click();
  await expect(changes.getByText('Acknowledged by Dr E2E').first()).toBeVisible();
});
