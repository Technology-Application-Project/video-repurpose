// Shared helpers for the recorded demos: a mocked Studio session (no cloud calls), a cursor overlay and full-screen cards.
// Captions are not drawn in the page: narration.mjs adds them in a bar under the app so they never cover the UI.
import { chromium } from 'playwright';
import fs from 'node:fs';

export const SIZE = { width: 1920, height: 968 }; // plus the 112px caption bar = 1920x1080
export const pause = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

const overlay = () => {
  const build = () => {
    if (document.getElementById('wt-cursor')) return;
    const style = document.createElement('style');
    style.textContent = `
      #wt-cursor{position:fixed;width:24px;height:24px;border:3px solid #c5f45d;border-radius:50%;background:rgba(197,244,93,.28);
        z-index:100000;pointer-events:none;transform:translate(-50%,-50%);left:-60px;top:-60px}
      .wt-ripple{position:fixed;width:20px;height:20px;border:3px solid #c5f45d;border-radius:50%;z-index:100000;pointer-events:none;
        transform:translate(-50%,-50%);animation:wt-ripple .6s ease-out forwards}
      @keyframes wt-ripple{to{width:80px;height:80px;opacity:0}}
      #wt-card{position:fixed;inset:0;z-index:99990;display:grid;place-items:center;background:radial-gradient(circle at 30% 20%,#243019,#0c0f09 70%);
        color:#f4f7ec;font-family:"IBM Plex Sans",system-ui,sans-serif;opacity:0;transition:opacity .5s;pointer-events:none}
      #wt-card.on{opacity:1}
      #wt-card .wt-inner{max-width:1100px;padding:0 60px;text-align:center}
      #wt-card .wt-brand{font:600 22px "IBM Plex Mono",ui-monospace,monospace;letter-spacing:.3em;text-transform:uppercase;color:#c5f45d}
      #wt-card h1{font-size:68px;line-height:1.1;margin:18px 0 0;font-weight:600}
      #wt-card h1 em{font-style:normal;color:#c5f45d}
      #wt-card p.wt-sub{font-size:26px;color:#b9c4a8;margin:22px 0 0}
      #wt-card ul{list-style:none;margin:36px 0 0;padding:0;display:grid;gap:16px;text-align:left;font-size:32px}
      #wt-card li{opacity:0;transform:translateY(14px);animation:wt-rise .6s ease-out forwards}
      #wt-card li b{color:#c5f45d;font-weight:600}
      #wt-card .wt-cols{display:grid;grid-template-columns:repeat(3,1fr);gap:22px;margin-top:40px;text-align:left}
      #wt-card .wt-col{border:1px solid #4a5e30;border-radius:14px;padding:22px 24px;background:rgba(36,48,25,.55);opacity:0;animation:wt-rise .6s ease-out forwards}
      #wt-card .wt-col h3{margin:0 0 10px;font-size:15px;letter-spacing:.14em;text-transform:uppercase;color:#c5f45d}
      #wt-card .wt-col p{margin:0;font-size:22px;line-height:1.4;color:#e3ead6}
      @keyframes wt-rise{to{opacity:1;transform:none}}`;
    document.head.append(style);
    const cursor = document.createElement('div');
    cursor.id = 'wt-cursor';
    const card = document.createElement('div');
    card.id = 'wt-card';
    document.body.append(card, cursor);
  };
  document.addEventListener('DOMContentLoaded', build);
  document.addEventListener('mousemove', (event) => {
    const cursor = document.getElementById('wt-cursor');
    if (cursor) { cursor.style.left = `${event.clientX}px`; cursor.style.top = `${event.clientY}px`; }
  }, true);
  document.addEventListener('mousedown', (event) => {
    const ripple = document.createElement('div');
    ripple.className = 'wt-ripple';
    ripple.style.left = `${event.clientX}px`;
    ripple.style.top = `${event.clientY}px`;
    document.body.append(ripple);
    setTimeout(() => ripple.remove(), 700);
  }, true);
};

/** Opens the recorded browser page with a mocked Studio session. `preview` delays are read when Generate preview is clicked. */
export async function startDemo({ base, workDir, previewMs = { stage: 3500, done: 7500 }, scale = 1, record = true, size = SIZE }) {
  fs.mkdirSync(workDir, { recursive: true });
  const browser = await chromium.launch();
  const context = await browser.newContext({ viewport: size, ...(record ? { recordVideo: { dir: workDir, size } } : {}), deviceScaleFactor: scale });
  await context.addInitScript(overlay);
  const page = await context.newPage();
  const started = Date.now();
  const api = context.request;
  const sources = await (await api.get(`${base}/api/sources`)).json();
  const library = await (await api.get(`${base}/api/exports`)).json();
  if (!sources.length || !library.exports.length) throw new Error('Needs one local source and one existing export.');
  const chosen = library.exports.find((item) => item.audience === 'Kids' && item.clip_count >= 2) || library.exports[0];
  const detail = await (await api.get(`${base}/api/exports/${chosen.id}`)).json();
  const clips = detail.clips.slice(0, 3);
  const delays = { ...previewMs };

  const plan = {
    version: '1.0', audience: 'Kids', revision: 'walkthrough', generated: { status: 'model_plan', provider: 'portkey', model: 'planner', note: '' },
    clip_count: clips.length, clip_count_reason: 'Three short clips fit the 30-60 second budget with room for the cards.',
    clips: clips.map((clip, index) => ({ id: clip.id, title: clip.title, start: clip.start, end: clip.end, seconds: Math.round(clip.end - clip.start), role: ['Setup', 'Example', 'Payoff'][index] || 'Idea' })),
    opening: { approach: 'question', idea: 'Open with the big question: why do some nations grow faster?' },
    bridges: clips.slice(1).map((clip, index) => ({ from_clip_id: clips[index].id, to_clip_id: clip.id, relationship: index ? 'contrast' : 'example', idea: index ? 'Contrast with a second case' : 'Show a first example' })),
    ending: { synthesis: 'Skills and schooling help economies grow.', action: 'Full lecture in description.' },
    timing: { speech_seconds: 30, opening_seconds: 3, bridge_seconds: 3, ending_seconds: 5, estimated_total: 41, budget: [30, 60], fits: true },
    risks: [],
  };
  let current = { id: 'walkthrough', revision: 0, filename: 'Economics lecture.mp4', duration: 600, source: sources[0].video, audience: 'Kids', aspect: '16:9',
    analyzed: false, confirmed: false, clips: [], selected: [], segments: [], preview: null, preview_current: false, approved: false,
    timeline: [], export_id: null, storyboard: {}, selection_mode: 'automatic', blueprint: null };
  const jobs = [];
  const bump = (changes) => { current = { ...current, ...changes, revision: current.revision + 1 }; };

  await page.route('**/api/**', async (route) => {
    const url = new URL(route.request().url());
    const method = route.request().method();
    if (url.pathname === '/api/jobs') return route.fulfill({ json: jobs });
    if (url.pathname === '/api/runs' && method === 'GET') return route.fulfill({ json: [current] });
    if (url.pathname === '/api/runs/walkthrough' && method === 'GET') return route.fulfill({ json: current });
    if (method === 'GET') return route.continue();
    if (url.pathname.startsWith('/api/runs/from-source/')) return route.fulfill({ json: current });
    const body = route.request().postDataJSON() || {};
    const finish = (action, result) => {
      const job = { id: `job-${action}-${jobs.length}`, run: 'walkthrough', action, stage: 'Complete', status: 'succeeded', created: '', result };
      jobs.push(job);
      return route.fulfill({ json: job });
    };
    if (url.pathname.endsWith('/selection')) {
      bump({ selected: body.clips.map((clip) => clip.id), clips: current.clips.map((clip) => body.clips.find((item) => item.id === clip.id) || clip) });
      return route.fulfill({ json: current });
    }
    if (url.pathname.endsWith('/approval')) {
      bump({ approved: true, reviewer: body.reviewer });
      return route.fulfill({ json: current });
    }
    if (url.pathname.endsWith('/analyze')) {
      bump({ analyzed: true, confirmed: true, clips, selected: clips.map((clip) => clip.id),
        segments: clips.map((clip) => ({ start: clip.start, end: clip.end, text: clip.excerpt || clip.takeaway || '' })) });
      return finish('analyze');
    }
    if (url.pathname.endsWith('/preview')) {
      const job = { id: `job-preview-${jobs.length}`, run: 'walkthrough', action: 'preview', stage: 'Planning the video blueprint', status: 'running', created: '' };
      jobs.push(job);
      bump({ blueprint: plan });
      setTimeout(() => { job.stage = 'Generating the storyboard and preview'; }, delays.stage);
      setTimeout(() => {
        job.status = 'succeeded'; job.stage = 'Complete';
        bump({ preview: detail.video, preview_current: true, storyboard: detail.storyboard, timeline: detail.timeline,
          blueprint: { ...plan, final: { clip_count: clips.length, same_clips: true, measured_total: Math.round(detail.duration * 10) / 10, difference: Math.round((detail.duration - 41) * 10) / 10 } } });
      }, delays.done);
      return route.fulfill({ json: job });
    }
    if (url.pathname.endsWith('/exports')) return finish('export', { export_id: detail.id });
    return route.continue();
  });

  const card = (html) => page.evaluate((markup) => {
    const node = document.getElementById('wt-card');
    if (!node) return;
    if (markup) node.innerHTML = `<div class="wt-inner">${markup}</div>`;
    node.classList.toggle('on', Boolean(markup));
  }, html);
  const move = async (locator, steps = 28) => {
    let box = await locator.boundingBox();
    // Keep the target fully inside the frame, clear of the top bar, so the viewer always sees what is clicked.
    if (!box || box.y < 76 || box.y + box.height > SIZE.height - 24) {
      await locator.evaluate((element) => element.scrollIntoView({ block: 'center' }));
      await pause(250);
      box = await locator.boundingBox();
    }
    if (box) await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2, { steps });
  };
  const click = async (locator) => {
    await move(locator);
    await pause(350);
    await locator.click();
  };
  const safely = async (label, action) => {
    try { await action(); } catch (error) { console.warn(`skipped: ${label}: ${error.message.split('\n')[0]}`); }
  };
  const finish = async () => {
    const video = page.video();
    await context.close();
    await browser.close();
    return video ? video.path() : null;
  };
  return { page, detail, clips, card, move, click, safely, finish, started, delays };
}
