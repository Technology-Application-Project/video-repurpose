// Records the stakeholder introduction: narrated scenes over a mocked Studio session, plus SRT, transcript and voice-over.
// Prereq: python tools/make_voiceover.py. Usage: STUDIO_URL=http://127.0.0.1:8791 node scripts/record-intro.mjs
import fs from 'node:fs';
import path from 'node:path';
import { pause, startDemo } from './demo-kit.mjs';
import { muxVideo, planCues, planTimeline, writeTranscripts } from './narration.mjs';

const BASE = process.env.STUDIO_URL || 'http://127.0.0.1:8791';
const ROOT = path.resolve('..');
const WORK = path.join(ROOT, 'work', 'stakeholder-intro');
const OUT = path.join(ROOT, 'docs', 'stakeholder-intro');
const timings = JSON.parse(fs.readFileSync(path.join(WORK, 'timings.json'), 'utf8'));
// 0.6 s of picture before the first word, 0.3 s between scenes, 1.2 s after the last.
const { scenes, total } = planTimeline(timings, { lead: 0.6, gap: 0.3, tail: 1.2 });
console.log(`timeline ${total.toFixed(1)}s`);

fs.rmSync(path.join(WORK, 'video'), { recursive: true, force: true });
const demo = await startDemo({ base: BASE, workDir: path.join(WORK, 'video') });
const { page, clips, card, move, click, safely, finish, delays } = demo;

await page.goto(`${BASE}/`);
await page.waitForSelector('text=One lecture. A new perspective.');
await page.mouse.move(720, 450);
const cards = {
  hook: '<div class="wt-brand">brevio studio</div><h1>One long lecture.<br><em>One short, reviewed video.</em></h1><p class="wt-sub">For the audience you choose</p>',
  problem: `<div class="wt-brand">The problem</div><h1 style="font-size:54px">Why good lectures stay unwatched</h1><ul>
    <li style="animation-delay:.9s"><b>Long</b> &nbsp;Few people watch an hour of video</li>
    <li style="animation-delay:4.6s"><b>Slow</b> &nbsp;Cutting highlights by hand is slow</li>
    <li style="animation-delay:6.6s"><b>Risky</b> &nbsp;Fully automatic tools are hard to trust</li></ul>`,
  safeguards: `<div class="wt-brand">Safeguards</div><div class="wt-cols">
    <div class="wt-col" style="animation-delay:.4s"><h3>Local</h3><p>Transcription, voices and rendering run on this computer</p></div>
    <div class="wt-col" style="animation-delay:3.4s"><h3>Gateway</h3><p>Short text requests only, capped at five per run</p></div>
    <div class="wt-col" style="animation-delay:7.7s"><h3>Human</h3><p>A named reviewer approves every export</p></div></div>`,
  close: '<div class="wt-brand">brevio studio</div><h1>Faster highlights.<br>Consistent quality.<br><em>A human in control.</em></h1><p class="wt-sub">Try it on one lecture</p>',
};
await card(cards.hook);
await pause(700);
const zero = Date.now(); // timeline zero: the title card is fully visible
const now = () => (Date.now() - zero) / 1000;
const until = (seconds) => pause(Math.max(0, (seconds - now()) * 1000));

const cues = planCues(scenes);

const actions = {
  hook: async () => {},
  problem: async () => { await card(cards.problem); },
  source: async (scene) => {
    await card('');
    await until(scene.start + 1.0);
    await click(page.getByRole('radio', { name: /Kids/ }));
    await safely('advanced', async () => {
      await click(page.getByText('Advanced settings'));
      const field = page.getByLabel('Source attribution URL');
      await move(field, 12);
      await field.click();
      await field.pressSequentially('https://example.edu/economics/week-3', { delay: 40 });
    });
  },
  clips: async () => {
    await click(page.getByRole('button', { name: 'Analyze source', exact: true }));
    await page.getByRole('heading', { name: 'Shape the story' }).waitFor({ timeout: 20000 });
    const keeps = page.getByRole('checkbox', { name: /^Keep / });
    await safely('toggle', async () => { await click(keeps.nth(clips.length - 1)); await pause(400); await click(keeps.nth(clips.length - 1)); });
    await safely('set in', async () => { await click(page.locator('.clip-select').nth(0)); await click(page.getByRole('button', { name: 'Set in' })); });
  },
  blueprint: async (scene) => {
    delays.stage = 2500;
    delays.done = Math.max(1500, (scene.seconds - 2.4) * 1000);
    await click(page.getByRole('button', { name: 'Generate preview', exact: true }));
  },
  studio: async () => {
    await page.getByRole('heading', { name: 'A final human check' }).waitFor({ timeout: 15000 });
    await safely('play', async () => {
      await page.evaluate(() => { const video = document.querySelector('video'); if (video) { video.muted = true; video.currentTime = 0; video.play().catch(() => {}); } });
    });
    const sections = page.locator('.timeline-section');
    for (let index = 0; index < Math.min(await sections.count(), 4); index += 1) {
      await safely(`section ${index}`, async () => { await click(sections.nth(index)); await pause(700); });
    }
    await safely('evidence', async () => { await click(page.getByRole('tab', { name: /Source evidence/ })); });
  },
  approval: async () => {
    await page.evaluate(() => { const video = document.querySelector('video'); if (video) video.pause(); });
    await safely('approval', async () => {
      const reviewer = page.getByRole('textbox', { name: 'Reviewer name' });
      await move(reviewer, 16);
      await reviewer.click();
      await reviewer.pressSequentially('Alex Morgan', { delay: 60 });
      await click(page.getByRole('checkbox', { name: /I reviewed the preview/ }));
      await click(page.getByRole('button', { name: 'Approve preview', exact: true }));
    });
  },
  export: async () => {
    await safely('export', async () => { await click(page.getByRole('button', { name: 'Export video', exact: true })); });
    await page.getByRole('heading', { name: 'Export library' }).waitFor({ timeout: 20000 });
    await safely('technical', async () => {
      await click(page.getByRole('tab', { name: /Technical check/ }));
      await click(page.locator('details.metric:not([open]) > summary').first());
    });
    await safely('structure', async () => {
      await click(page.getByRole('tab', { name: /Structure check/ }));
      await click(page.locator('details.metric:not([open]) > summary').first());
      await page.locator('.plan-follow').scrollIntoViewIfNeeded();
    });
  },
  safeguards: async () => { await card(cards.safeguards); },
  close: async () => { await card(cards.close); },
};

const overruns = [];
for (const scene of scenes) {
  await until(scene.start);
  await actions[scene.id](scene);
  const late = now() - scene.end;
  if (late > 0) overruns.push(`${scene.id} ${late.toFixed(1)}s late`);
  await until(scene.end);
}
await until(total);
const source = await finish();
if (overruns.length) console.warn(`scene actions overran their narration: ${overruns.join(', ')}`);

writeTranscripts({ outDir: OUT, title: 'Brevio Studio: stakeholder introduction', timings, scenes, cues, total });
const mp4 = await muxVideo({ source, trimStart: (zero - demo.started) / 1000, cues, total, outDir: OUT, name: 'brevio-studio-intro.mp4', workDir: WORK });
console.log(mp4, `${(fs.statSync(mp4).size / 1e6).toFixed(1)} MB`);
