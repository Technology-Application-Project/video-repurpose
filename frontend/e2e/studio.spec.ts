import { expect, test, type Page } from '@playwright/test';
import type { Blueprint, ExportDetail, ExportItem, Job, Run, Source } from '../src/types';

async function overflow(page: Page) {
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
}

test('blueprint panel shows the plan on the clips stage and planned versus result in the studio', async ({ page }, info) => {
  const errors: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  const clips = [0, 1, 2].map(index => ({ id: `c${index}`, title: `Idea ${index}`, takeaway: '', start: index * 30, end: index * 30 + 10, excerpt: `Excerpt ${index}` }));
  const plan: Blueprint = {
    version: '1.0', audience: 'Kids', revision: 'r1',
    generated: { status: 'model_plan', provider: 'portkey', model: 'm', note: '' },
    clip_count: 3, clip_count_reason: 'Three clips fit the budget',
    clips: clips.map((clip, index) => ({ id: clip.id, title: clip.title, start: clip.start, end: clip.end, seconds: 10, role: ['Setup', 'Development', 'Payoff'][index] })),
    opening: { approach: 'contrast', idea: 'Ask why similar nations differ' },
    bridges: [{ from_clip_id: 'c0', to_clip_id: 'c1', relationship: 'example', idea: 'Show a first example' },
      { from_clip_id: 'c1', to_clip_id: 'c2', relationship: 'contrast', idea: 'Contrast with a second case' }],
    ending: { synthesis: 'Skills shape growth', action: 'Compare two countries' },
    timing: { speech_seconds: 30, opening_seconds: 3, bridge_seconds: 3, ending_seconds: 5, estimated_total: 41, budget: [30, 60], fits: true },
    risks: ['Close to the maximum: narrated cards usually run longer than their minimums.'],
  };
  let current: Run = { id: 'browser-test', revision: 3, filename: 'Browser test lecture.mp4', duration: 600,
    source: '/api/runs/browser-test/media/source', audience: 'Kids', aspect: '16:9', analyzed: true, confirmed: true,
    clips, selected: clips.map(clip => clip.id), segments: [], preview: null, preview_current: false, approved: false,
    timeline: [], export_id: null, storyboard: {}, selection_mode: 'automatic', blueprint: plan };
  const jobs: Job[] = [{ id: 'job-preview', run: 'browser-test', action: 'preview', stage: 'Planning the video blueprint', status: 'running', created: '' }];
  await page.route('**/api/**', async route => {
    const path = new URL(route.request().url()).pathname;
    if (route.request().method() !== 'GET') throw new Error('No mutations allowed in blueprint test');
    if (path === '/api/jobs') return route.fulfill({ json: jobs });
    if (path === '/api/runs') return route.fulfill({ json: [current] });
    if (path === '/api/runs/browser-test') return route.fulfill({ json: current });
    if (path.startsWith('/api/runs/browser-test/media')) return route.fulfill({ status: 404, body: '' });
    return route.continue();
  });
  await page.goto('/#view=clips&run=browser-test');
  const panel = page.getByRole('region', { name: 'Video blueprint' });
  await expect(panel).toBeVisible();
  await expect(panel.getByRole('heading', { name: '3 clips · about 41s' })).toBeVisible();
  await expect(panel.getByText('Ask why similar nations differ')).toBeVisible();
  await expect(panel.getByText('Contrast with a second case')).toBeVisible();
  await expect(panel.getByText('Within 30-60s')).toBeVisible();
  await expect(panel.getByText('Model plan')).toBeVisible();
  await expect(panel.getByText(/Close to the maximum/)).toBeVisible();
  await expect(panel.getByText(/Result:/)).toHaveCount(0);
  await overflow(page);
  await page.screenshot({ path: info.outputPath('blueprint-clips.png'), fullPage: true });
  current = { ...current, revision: 4, preview: '/api/runs/browser-test/media/preview', preview_current: true,
    blueprint: { ...plan, final: { clip_count: 3, same_clips: true, measured_total: 44.2, difference: 3.2 } } };
  await expect(panel.getByText('Result: 3 clips, 44s measured (+3s vs plan)')).toBeVisible({ timeout: 15000 });
  jobs[0] = { ...jobs[0], status: 'succeeded', stage: 'Complete' };
  await page.getByRole('button', { name: '3 Studio', exact: true }).click();
  await page.getByText('Blueprint', { exact: true }).click();
  await expect(page.getByRole('region', { name: 'Video blueprint' }).getByText('Result: 3 clips, 44s measured (+3s vs plan)')).toBeVisible();
  await overflow(page);
  expect(errors).toEqual([]);
});

test('export tab shows both the technical check and the structure check with the blueprint comparison', async ({ page }, info) => {
  const errors: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  const item: ExportItem = { id: 'mock-export', title: 'Mock export', audience: 'Kids', aspect: '16:9', created: 1790000000, duration: 40.5,
    clip_count: 3, test_export: true, approved: false, video: '/api/exports/mock-export/files/compilation.mp4', poster: null };
  const detail: ExportDetail = { ...item, folder: '/tmp/mock-export', files: ['compilation.mp4', 'captions.srt', 'post.txt'], timeline: [], clips: [],
    storyboard: {}, post: 'Post copy',
    assessment: { metric_version: '2.2', technical_status: 'passed', counts: { pass: 1, fail: 0 }, publication_status: 'not_assessed',
      metrics: [{ id: 'loudness', label: 'Loudness is inside the range', status: 'pass', threshold: 'target', value: -16,
        checks: 'Overall volume is in a comfortable range.', exported: '-16.0 LUFS', fix: null }] },
    structure: { version: '1.0', status: 'needs_attention', counts: { pass: 2, fail: 0, review: 1, not_applicable: 0, unavailable: 0 }, note: '', video_seconds: 40.5,
      metrics: [
        { id: 'section_order', label: 'Opening, clips, bridges and ending are in order', status: 'pass', threshold: 'order', value: {},
          checks: 'The video starts with the opening card and finishes with the ending card.', exported: 'Opening → Clip 1 → Bridge → Clip 2 → Ending', fix: null },
        { id: 'clip_order', label: 'Clips keep the approved order', status: 'pass', threshold: 'order', value: {},
          checks: 'The clips play in the order you approved.', exported: 'Idea 0 → Idea 1', fix: null },
        { id: 'blueprint_clip_count', label: 'Clip count matches the blueprint', status: 'review', threshold: 'planned count', value: { planned: 3, exported: 2 },
          checks: 'The video uses the number of clips the blueprint planned.', exported: 'Planned 3 · exported 2',
          fix: 'A difference is fine if you changed the clips on purpose; otherwise check the selection.' },
      ],
      blueprint: { status: 'model_plan', revision: 'r1', rows: [
        { section: 'Opening', planned: 'Ask why similar nations differ', actual: 'Why do nations differ?', match: 'follows' },
        { section: 'Bridge 1', planned: 'Show a first example', actual: 'How does idea 1 connect?', match: 'differs' } ] } } };
  await page.route('**/api/**', async route => {
    const path = new URL(route.request().url()).pathname;
    if (route.request().method() !== 'GET') throw new Error('No mutations allowed in the checks test');
    if (path === '/api/exports') return route.fulfill({ json: { exports: [item] } });
    if (path === '/api/exports/mock-export') return route.fulfill({ json: detail });
    if (path === '/api/jobs' || path === '/api/runs') return route.fulfill({ json: [] });
    if (path.startsWith('/api/exports/mock-export/')) return route.fulfill({ status: 404, body: '' });
    return route.continue();
  });
  await page.goto('/#view=export');
  const technical = page.getByRole('tab', { name: /Technical check/ });
  const structure = page.getByRole('tab', { name: /Structure check/ });
  await expect(technical).toHaveAttribute('aria-selected', 'true');
  await expect(technical).toContainText('Passed');
  await expect(structure).toContainText('Needs attention');
  await expect(page.getByText('Checks passed')).toBeVisible();
  await expect(page.getByText('Loudness is inside the range')).toBeVisible();
  await page.getByText('Loudness is inside the range').click();
  await expect(page.getByText('Overall volume is in a comfortable range.')).toBeVisible();
  await expect(page.getByText('-16.0 LUFS')).toBeVisible();
  await structure.click();
  await expect(structure).toHaveAttribute('aria-selected', 'true');
  await expect(page.getByText('2 passed · 0 failed · 1 to review')).toBeVisible();
  await expect(page.getByText('Clip count matches the blueprint')).toBeVisible();
  await expect(page.getByText('Planned 3 · exported 2')).toBeVisible();
  await expect(page.getByText('The video uses the number of clips the blueprint planned.')).toBeVisible();
  await expect(page.getByText('A difference is fine if you changed the clips on purpose; otherwise check the selection.')).toBeVisible();
  await expect(page.getByText('To review', { exact: true })).toBeVisible();
  const follow = page.getByRole('region', { name: 'Does the story follow the plan' });
  await expect(follow.getByText('Follows the plan')).toBeVisible();
  await expect(follow.getByText('Differs', { exact: true })).toBeVisible();
  await expect(page.getByText('Ask why similar nations differ')).toBeHidden();
  await follow.getByText('Compare the wording').click();
  await expect(page.getByText('Ask why similar nations differ')).toBeVisible();
  await expect(page.getByText('How does idea 1 connect?')).toBeVisible();
  await expect(page.getByRole('button', { name: 'Run technical check' })).toBeVisible();
  await overflow(page);
  await page.screenshot({ path: info.outputPath('structure-check.png'), fullPage: true });
  expect(errors).toEqual([]);
});

test('real media, library, timeline and mobile inspector', async ({ page }, info) => {
  const errors: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.route('**/api/**', async route => {
    if (route.request().method() !== 'GET') throw new Error('No mutations allowed in media smoke test');
    await route.continue();
  });
  await page.goto('/');
  await expect(page.getByRole('heading', { name: 'One lecture. A new perspective.' })).toBeVisible();
  await expect(page.locator('.export-card').first()).toBeVisible();
  await overflow(page);
  await page.screenshot({ path: info.outputPath('source.png'), fullPage: true });
  await page.getByRole('button', { name: '4 Export', exact: true }).click();
  await expect(page.getByRole('link', { name: 'Download MP4' })).toBeVisible();
  await expect(page.getByRole('tab', { name: /Structure check/ })).toBeVisible();
  const media = page.locator('video');
  await expect.poll(() => media.evaluate(video => video.readyState)).toBeGreaterThanOrEqual(2);
  await media.evaluate(video => { video.currentTime = 5; });
  await expect.poll(() => media.evaluate(video => !video.seeking)).toBe(true);
  const pixels = await media.evaluate(video => {
    const canvas = document.createElement('canvas');
    canvas.width = 64; canvas.height = 36;
    const context = canvas.getContext('2d')!;
    context.drawImage(video, 0, 0, 64, 36);
    const samples = context.getImageData(0, 0, 64, 36).data;
    let sum = 0;
    for (let index = 0; index < samples.length; index += 4) sum += samples[index] + samples[index + 1] + samples[index + 2];
    return sum / (64 * 36 * 3);
  });
  expect(pixels).toBeGreaterThan(5);
  await overflow(page);
  await page.screenshot({ path: info.outputPath('export.png'), fullPage: true });
  await page.getByRole('button', { name: 'Open in studio', exact: true }).click();
  await page.locator('.timeline-section').nth(1).click();
  await expect.poll(() => media.evaluate(video => video.currentTime)).toBeGreaterThan(0);
  await expect.poll(() => media.evaluate(video => video.readyState >= 2 && !video.seeking)).toBe(true);
  if (info.project.name === 'mobile') {
    await page.getByRole('button', { name: 'Inspector', exact: true }).click();
    await expect(page.getByRole('dialog')).toBeVisible();
    await page.getByRole('button', { name: 'Close inspector' }).click();
  }
  await overflow(page);
  await page.screenshot({ path: info.outputPath('studio.png'), fullPage: true });
  expect(errors).toEqual([]);
});

test('source to export interaction, all generation and mutations mocked', async ({ page, request }, info) => {
  const errors: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  const sources = await (await request.get('/api/sources')).json() as Source[];
  const library = await (await request.get('/api/exports')).json();
  test.skip(!sources.length || !library.exports.length, 'Requires one local source and one existing export');
  const detail = await (await request.get(`/api/exports/${library.exports[0].id}`)).json() as ExportDetail;
  const clips = detail.clips.slice(0, 3);
  let current: Run = { id: 'browser-test', revision: 0, filename: 'Browser test lecture.mp4', duration: 600,
    source: sources[0].video, audience: 'Kids', aspect: '16:9', analyzed: false, confirmed: false,
    clips: [], selected: [], segments: [], preview: null, preview_current: false, approved: false,
    timeline: [], export_id: null, storyboard: {}, selection_mode: 'automatic' };
  const jobs: Job[] = [];
  const calls: string[] = [];
  await page.route('**/api/**', async route => {
    const path = new URL(route.request().url()).pathname;
    const method = route.request().method();
    if (path === '/api/jobs') return route.fulfill({ json: jobs });
    if (path === '/api/runs' && method === 'GET') return route.fulfill({ json: [current] });
    if (path === '/api/runs/browser-test' && method === 'GET') return route.fulfill({ json: current });
    if (method === 'GET') return route.continue();
    calls.push(path);
    if (path.startsWith('/api/runs/from-source/')) return route.fulfill({ json: current });
    const body = route.request().postDataJSON();
    expect(route.request().headers()['x-studio-token']).toBeTruthy();
    expect(body.revision).toBe(current.revision);
    expect(body.key).toBeTruthy();
    if (path.endsWith('/selection')) {
      current = { ...current, revision: current.revision + 1, selected: body.clips.map((clip: { id: string }) => clip.id), clips: body.clips };
      return route.fulfill({ json: current });
    }
    if (path.endsWith('/approval')) {
      expect(body.reviewer).toBe('Browser test reviewer');
      expect(body.attested).toBe(true);
      current = { ...current, revision: current.revision + 1, approved: true, reviewer: body.reviewer };
      return route.fulfill({ json: current });
    }
    let action = '';
    if (path.endsWith('/analyze')) {
      expect(body.options.audience).toBe('Kids');
      current = { ...current, revision: current.revision + 1, analyzed: true, confirmed: true,
        clips, selected: clips.map(clip => clip.id), segments: clips.map(clip => ({ start: clip.start, end: clip.end, text: clip.excerpt || clip.takeaway })) };
      action = 'analyze';
    } else if (path.endsWith('/preview')) {
      current = { ...current, revision: current.revision + 1, preview: detail.video, preview_current: true, storyboard: detail.storyboard, timeline: detail.timeline };
      action = 'preview';
    } else if (path.endsWith('/exports')) {
      expect(current.approved).toBe(true);
      action = 'export';
    } else throw new Error(`Unexpected mutation blocked: ${method} ${path}`);
    const job: Job = { id: `job-${action}`, run: current.id, action, stage: 'Complete', status: 'succeeded', created: '', result: action === 'export' ? { export_id: detail.id } : undefined };
    jobs.push(job);
    return route.fulfill({ json: job });
  });
  await page.goto('/');
  await page.getByRole('radio', { name: 'Kids Curious minds, ages 8-12' }).click();
  await page.getByRole('button', { name: 'Analyze source', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Shape the story' })).toBeVisible();
  const first = clips[0];
  await page.getByRole('checkbox', { name: `Keep ${first.title}`, exact: true }).uncheck();
  await page.getByRole('checkbox', { name: `Keep ${first.title}`, exact: true }).check();
  await overflow(page);
  await page.screenshot({ path: info.outputPath('clips.png'), fullPage: true });
  await page.getByRole('button', { name: 'Generate preview', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'A final human check' })).toBeVisible();
  const approve = page.getByRole('button', { name: 'Approve preview', exact: true });
  await expect(approve).toBeDisabled();
  await page.getByRole('textbox', { name: 'Reviewer name' }).fill('Browser test reviewer');
  await expect(approve).toBeDisabled();
  await page.getByRole('checkbox', { name: /I reviewed the preview/ }).check();
  await approve.click();
  await page.getByRole('button', { name: 'Export video', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Export library' })).toBeVisible();
  expect(calls.filter(path => path.endsWith('/preview'))).toHaveLength(1);
  expect(calls.filter(path => path.endsWith('/exports'))).toHaveLength(1);
  expect(calls.some(path => path.endsWith('/selection'))).toBe(true);
  expect(errors).toEqual([]);
});