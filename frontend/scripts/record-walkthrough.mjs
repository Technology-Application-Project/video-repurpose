// Records the narrated Brevio Studio usage walkthrough. Every sentence of the narration starts exactly one on-screen action,
// over a mocked session (no cloud calls), so the transcript and the demo match step for step.
// Prereq: python tools/make_voiceover.py docs/walkthrough/script.json work/walkthrough-voice
// Usage: STUDIO_URL=http://127.0.0.1:8791 node scripts/record-walkthrough.mjs
import fs from 'node:fs';
import path from 'node:path';
import { pause, startDemo } from './demo-kit.mjs';
import { muxVideo, planCues, planTimeline, writeTranscripts } from './narration.mjs';

const BASE = process.env.STUDIO_URL || 'http://127.0.0.1:8791';
const ROOT = path.resolve('..');
const VOICE = path.join(ROOT, 'work', 'walkthrough-voice');
const OUT = path.join(ROOT, 'docs', 'walkthrough');
const timings = JSON.parse(fs.readFileSync(path.join(VOICE, 'timings.json'), 'utf8'));
const { scenes, total } = planTimeline(timings, { lead: 0.8, gap: 0.3, tail: 1.5 });
const cues = planCues(scenes);
console.log(`timeline ${total.toFixed(1)}s`);

fs.rmSync(path.join(VOICE, 'video'), { recursive: true, force: true });
const demo = await startDemo({ base: BASE, workDir: path.join(VOICE, 'video') });
const { page, clips, move, click, finish, delays } = demo;

await page.goto(`${BASE}/`);
await page.waitForSelector('text=One lecture. A new perspective.');
await page.mouse.move(720, 450);
await pause(600);
const zero = Date.now();
const now = () => (Date.now() - zero) / 1000;
const until = (seconds) => pause(Math.max(0, (seconds - now()) * 1000));
const player = (play) => page.evaluate((on) => {
  const video = document.querySelector('video');
  if (!video) return;
  video.muted = true;
  if (on) { video.currentTime = 0; video.play().catch(() => {}); } else video.pause();
}, play);
const hoverEach = async (locators, hold = 350) => {
  for (const locator of locators) { await move(locator, 20); await pause(hold); }
};
const nav = page.locator('nav.workflow-nav button');
const advanced = page.locator('details.advanced');
const blueprint = page.locator('section.blueprint').first();
const toTop = () => page.evaluate(() => window.scrollTo(0, 0));
const keeps = page.getByRole('checkbox', { name: /^Keep / });
const sections = page.locator('.timeline-section');
const detailTab = (name) => page.getByRole('tab', { name });
const firstClosedMetric = page.locator('details.metric:not([open]) > summary').first();

// One function per narration sentence, in the order of docs/walkthrough/script.json.
const actions = {
  open: [
    async () => {},
    async () => { await hoverEach([nav.nth(0), nav.nth(1), nav.nth(2), nav.nth(3)], 450); },
  ],
  audience: [
    async () => { await hoverEach([page.getByRole('radio', { name: /Researcher/ }), page.getByRole('radio', { name: /General public/ }), page.getByRole('radio', { name: /Kids/ })], 500); },
    async () => { await click(page.getByRole('radio', { name: /Kids/ })); },
  ],
  advanced: [
    async () => { await click(page.locator('summary').filter({ hasText: 'Advanced settings' })); },
    async () => { await move(advanced.getByLabel('Aspect ratio'), 20); },
    async () => { await move(advanced.getByLabel('Transcription quality'), 20); },
    async () => { await move(advanced.getByLabel('Local model'), 20); },
    async () => { await hoverEach([advanced.getByLabel('Min clip (s)'), advanced.getByLabel('Max clip (s)')], 500); },
    async () => {
      const field = advanced.getByLabel('Source attribution URL');
      await move(field, 14);
      await field.click();
      await field.pressSequentially('https://example.edu/economics/week-3-lecture', { delay: 25 });
    },
    async () => {},
  ],
  analyze: [
    async () => {
      await click(page.getByRole('button', { name: 'Analyze source', exact: true }));
      await page.getByRole('heading', { name: 'Shape the story' }).waitFor({ timeout: 20000 });
      await toTop();
    },
    async () => { await hoverEach(Array.from({ length: clips.length }, (_, index) => page.locator('.clip-select').nth(index)), 450); },
  ],
  clips: [
    async () => { await hoverEach(Array.from({ length: clips.length }, (_, index) => keeps.nth(index)), 350); },
    async () => {
      await click(keeps.nth(clips.length - 1));
      await pause(900);
      await click(keeps.nth(clips.length - 1));
    },
  ],
  trim: [
    async () => { await click(page.locator('.clip-select').nth(0)); },
    async () => { await player(true); await click(page.getByRole('button', { name: 'Set in' })); },
    async () => { await click(page.getByRole('button', { name: 'Set out' })); },
  ],
  generate: [
    async (scene) => {
      await player(false);
      delays.stage = 2500;
      delays.done = Math.max(1500, (scene.sentences[4].start - scene.sentences[0].start) * 1000);
      await click(page.getByRole('button', { name: 'Generate preview', exact: true }));
    },
    async () => { await blueprint.waitFor({ timeout: 8000 }); await move(blueprint.locator('h2'), 30); },
    async () => { await move(blueprint.locator('.blueprint-bar'), 30); },
    async () => { await hoverEach(await blueprint.locator('.blueprint-ideas article').all(), 450); },
    async () => {},
  ],
  studio: [
    async () => {
      await page.getByRole('heading', { name: 'A final human check' }).waitFor({ timeout: 15000 });
      await toTop();
      await move(page.locator('video').first(), 20);
      await player(true);
    },
    async () => {
      const last = (await sections.count()) - 1;
      for (const index of [0, 1, 2, last]) { await click(sections.nth(index)); await pause(300); }
    },
  ],
  evidence: [
    async () => { await player(false); await click(detailTab(/Source evidence/)); },
    async () => {
      await click(page.locator('details.blueprint-details > summary'));
      await move(blueprint.locator('.blueprint-final'), 30);
    },
  ],
  approval: [
    async () => {
      const reviewer = page.getByRole('textbox', { name: 'Reviewer name' });
      await move(reviewer, 16);
      await reviewer.click();
      await reviewer.pressSequentially('Alex Morgan', { delay: 60 });
    },
    async () => { await click(page.getByRole('checkbox', { name: /I reviewed the preview/ })); },
    async () => { await click(page.getByRole('button', { name: 'Approve preview', exact: true })); },
  ],
  export: [
    async () => {
      await click(page.getByRole('button', { name: 'Export video', exact: true }));
      await page.getByRole('heading', { name: 'Export library' }).waitFor({ timeout: 20000 });
      await toTop();
    },
    async () => { await hoverEach([page.getByRole('link', { name: /Download MP4/ }), page.getByRole('link', { name: /ZIP package/ })], 500); },
    async () => { await click(page.locator('summary').filter({ hasText: 'Post copy' })); },
  ],
  technical: [
    async () => { await click(detailTab(/Technical check/)); },
    async () => { await click(firstClosedMetric); },
  ],
  structure: [
    async () => { await click(detailTab(/Structure check/)); },
    async () => { await hoverEach(await page.locator('details.metric > summary').filter({ hasText: /blueprint/i }).all(), 350); },
    async () => { await click(page.locator('.plan-compare > summary')); },
    async () => { await hoverEach(await page.locator('.plan-follow li').all(), 350); },
  ],
  close: [
    async () => { await toTop(); await hoverEach([nav.nth(0), nav.nth(1), nav.nth(2), nav.nth(3)], 500); },
    async () => {},
  ],
};

const problems = [];
for (const scene of scenes) {
  const steps = actions[scene.id];
  if (steps?.length !== scene.sentences.length) throw new Error(`${scene.id}: ${scene.sentences.length} sentences need ${scene.sentences.length} actions`);
  for (const [index, sentence] of scene.sentences.entries()) {
    await until(sentence.start);
    const late = now() - sentence.start;
    if (late > 0.5) problems.push(`${scene.id} #${index + 1} started ${late.toFixed(1)}s late`);
    try { await steps[index](scene); } catch (error) { problems.push(`${scene.id} #${index + 1} failed: ${error.message.split('\n')[0]}`); }
  }
  await until(scene.end);
}
await until(total);
const source = await finish();
if (problems.length) console.warn(`attention:\n  ${problems.join('\n  ')}`);

fs.mkdirSync(OUT, { recursive: true });
writeTranscripts({ outDir: OUT, title: 'Brevio Studio: usage walkthrough', timings, scenes, cues, total });
const mp4 = await muxVideo({ source, trimStart: (zero - demo.started) / 1000, cues, total, outDir: OUT, name: 'brevio-studio-walkthrough.mp4', workDir: VOICE });
console.log(mp4, `${(fs.statSync(mp4).size / 1e6).toFixed(1)} MB`);
