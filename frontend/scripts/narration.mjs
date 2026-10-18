// Shared narration helpers for the recorded demos: sentence timeline, caption bar, transcripts and the final voiced MP4.
import { execFileSync } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';
import { chromium } from 'playwright';
import { SIZE } from './demo-kit.mjs';

const SENTENCE_GAP = 0.2; // seconds of silence between sentences of one scene
const BAR = 112; // caption bar under the app, so captions never cover the UI

/** Lays scenes out one after another: `lead` before the first word, `gap` between scenes, `tail` after the last. */
export function planTimeline(timings, { lead, gap, tail }) {
  let cursor = lead;
  const scenes = timings.scenes.map((scene) => {
    const start = cursor;
    let at = start;
    const sentences = scene.sentences.map((sentence) => {
      const planned = { ...sentence, start: at, end: at + sentence.seconds };
      at = planned.end + SENTENCE_GAP;
      return planned;
    });
    const end = sentences.at(-1).end;
    cursor = end + gap;
    return { ...scene, sentences, start, end, seconds: end - start };
  });
  return { scenes, total: scenes.at(-1).end + tail };
}

export const planCues = (scenes) => scenes.flatMap((scene) => scene.sentences.map((sentence, index) => ({ scene, index, ...sentence })));

const stamp = (seconds) => {
  const ms = Math.round(seconds * 1000);
  const part = (value, size) => String(value).padStart(size, '0');
  return `${part(Math.floor(ms / 3600000), 2)}:${part(Math.floor(ms / 60000) % 60, 2)}:${part(Math.floor(ms / 1000) % 60, 2)},${part(ms % 1000, 3)}`;
};
const minute = (seconds) => `${String(Math.floor(seconds / 60)).padStart(2, '0')}:${String(Math.floor(seconds % 60)).padStart(2, '0')}`;

export function writeTranscripts({ outDir, title, timings, scenes, cues, total }) {
  fs.writeFileSync(path.join(outDir, 'transcript.srt'), cues.map((cue, index) => `${index + 1}\n${stamp(cue.start)} --> ${stamp(cue.end)}\n${cue.text}\n`).join('\n'));
  fs.writeFileSync(path.join(outDir, 'transcript.txt'), [
    `${title} (about ${Math.round(total)} seconds)`,
    `Voice-over: local Kokoro voice ${timings.voice}, speed ${timings.speed}`, '',
    ...scenes.flatMap((scene) => [`[${minute(scene.start)}] ${scene.label}`, ...scene.sentences.map((sentence) => `  ${minute(sentence.start)}  ${sentence.text}`), '']),
  ].join('\n'));
}

const escapeHtml = (value) => value.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');

/** One PNG per cue, rendered with the same browser so the text wraps and scales cleanly. */
async function renderCaptionBars(cues, dir) {
  fs.mkdirSync(dir, { recursive: true });
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: SIZE.width, height: BAR } });
  const files = [];
  for (const [index, cue] of cues.entries()) {
    await page.setContent(`<body style="margin:0;width:${SIZE.width}px;height:${BAR}px;box-sizing:border-box;background:#0c0f09;border-top:2px solid #5a743b;
      display:flex;flex-direction:column;justify-content:center;align-items:center;font-family:'IBM Plex Sans',Helvetica,Arial,sans-serif;text-align:center">
      <div style="font-size:15px;letter-spacing:.14em;text-transform:uppercase;color:#c5f45d;margin-bottom:6px">${escapeHtml(cue.scene.label)}</div>
      <div style="font-size:28px;line-height:1.25;color:#f4f7ec;max-width:1700px">${escapeHtml(cue.text)}</div></body>`);
    const file = path.join(dir, `bar-${String(index).padStart(3, '0')}.png`);
    await page.screenshot({ path: file });
    files.push(file);
  }
  await browser.close();
  return files;
}

/** Places each sentence's audio at its start, then joins it to the recording with the caption bar beneath the app. */
export async function muxVideo({ source, trimStart, cues, total, outDir, name, workDir }) {
  const voiceInputs = cues.flatMap((cue) => ['-i', cue.audio]);
  const delayed = cues.map((cue, index) => `[${index + 1}:a]adelay=${Math.round(cue.start * 1000)}|${Math.round(cue.start * 1000)}[a${index}]`);
  const mix = `${delayed.join(';')};${cues.map((_, index) => `[a${index}]`).join('')}amix=inputs=${cues.length}:normalize=0:duration=longest,apad,atrim=0:${total.toFixed(3)}[voice]`;
  const voice = path.join(outDir, 'voice-over.wav');
  execFileSync('ffmpeg', ['-y', '-v', 'error', '-f', 'lavfi', '-i', 'anullsrc=r=48000:cl=mono', ...voiceInputs, '-filter_complex', mix, '-map', '[voice]', '-ar', '48000', '-ac', '1', voice]);

  const bars = await renderCaptionBars(cues, path.join(workDir, 'captions'));
  const height = SIZE.height + BAR;
  const overlays = cues.map((cue, index) => `[v${index}][${index + 2}:v]overlay=0:${SIZE.height}:enable='between(t,${cue.start.toFixed(3)},${(cue.end + 0.15).toFixed(3)})'[v${index + 1}]`);
  const graph = [`[0:v]pad=${SIZE.width}:${height}:0:0:color=0x0c0f09[v0]`, ...overlays].join(';');
  const mp4 = path.join(outDir, name);
  execFileSync('ffmpeg', ['-y', '-v', 'error', '-ss', String(Math.max(0, trimStart)), '-i', source, '-i', voice, ...bars.flatMap((file) => ['-i', file]),
    '-filter_complex', graph, '-map', `[v${cues.length}]`, '-map', '1:a:0', '-t', total.toFixed(3), '-c:v', 'libx264', '-preset', 'slow', '-crf', '22', '-pix_fmt', 'yuv420p',
    '-r', '30', '-c:a', 'aac', '-b:a', '160k', '-movflags', '+faststart', mp4]);
  return mp4;
}
