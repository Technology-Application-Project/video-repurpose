// Captures high-definition (2x) stills of the real Studio UI for the pitch, using the mocked session (no cloud calls).
// Usage: STUDIO_URL=http://127.0.0.1:8791 node scripts/capture-pitch-stills.mjs
import path from 'node:path';
import { pause, startDemo } from './demo-kit.mjs';

const BASE = process.env.STUDIO_URL || 'http://127.0.0.1:8791';
const OUT = path.resolve('..', 'docs', 'pitch', 'stills');
const demo = await startDemo({ base: BASE, workDir: path.resolve('..', 'work', 'pitch', 'capture'), scale: 2, record: false, size: { width: 1920, height: 1080 } });
const { page, click, safely, finish } = demo;
const shot = async (name) => { await pause(700); await page.screenshot({ path: path.join(OUT, `${name}.png`) }); console.log('saved', name); };

await page.goto(`${BASE}/`);
await page.waitForSelector('text=One lecture. A new perspective.');
// Lecture footage is third-party (rights unverified), so the UI is shown with the picture blurred.
await page.addStyleTag({ content: '#wt-cursor{display:none!important} img[src]{filter:blur(16px)}' });
const videoBlur = await page.addStyleTag({ content: 'video{filter:blur(26px) brightness(.75)}' });
await shot('ui-source');
await page.evaluate(() => document.querySelector('.guide-grid')?.scrollIntoView({ block: 'start' }));
await shot('ui-pipeline');
await page.locator('.guide').first().screenshot({ path: path.join(OUT, 'ui-guide-pipeline.png') });
await page.locator('.privacy').screenshot({ path: path.join(OUT, 'ui-guide-privacy.png') });
console.log('saved ui-guide-pipeline, ui-guide-privacy');
await page.evaluate(() => window.scrollTo(0, 0));

await safely('kids', async () => { await click(page.getByRole('radio', { name: /Kids/ })); });
await pause(500);
await page.locator('.source-settings').screenshot({ path: path.join(OUT, 'ui-audience.png') });
console.log('saved ui-audience');
await click(page.getByRole('button', { name: 'Analyze source', exact: true }));
await page.getByRole('heading', { name: 'Shape the story' }).waitFor({ timeout: 20000 });
await shot('ui-clips');

demo.delays.stage = 6000;
demo.delays.done = 9000;
await click(page.getByRole('button', { name: 'Generate preview', exact: true }));
await safely('blueprint', async () => { await page.getByText('Three short clips fit').waitFor({ timeout: 8000 }); });
await shot('ui-blueprint');
await page.getByRole('heading', { name: 'A final human check' }).waitFor({ timeout: 15000 });
// The preview opens on the generated illustration card, not lecture footage.
await page.evaluate(() => { const video = document.querySelector('video'); if (video) { video.pause(); video.currentTime = 1.5; } });
await videoBlur.evaluate((node) => { node.disabled = true; });
await pause(1200);
await shot('ui-studio');

await safely('approval', async () => {
  await page.getByRole('textbox', { name: 'Reviewer name' }).fill('Alex Morgan');
  await click(page.getByRole('checkbox', { name: /I reviewed the preview/ }));
  await shot('ui-approval');
  await click(page.getByRole('button', { name: 'Approve preview', exact: true }));
});
await safely('export', async () => {
  await click(page.getByRole('button', { name: 'Export video', exact: true }));
  await page.getByRole('heading', { name: 'Export library' }).waitFor({ timeout: 20000 });
  await click(page.getByRole('tab', { name: /Structure check/ }));
  await page.evaluate(() => window.scrollTo(0, 0));
  await shot('ui-export');
  await click(page.getByRole('tab', { name: /How to judge/ }));
  await pause(500);
  await page.locator('.assessment-panel').screenshot({ path: path.join(OUT, 'ui-rubric.png') });
  console.log('saved ui-rubric');
});
await finish();
