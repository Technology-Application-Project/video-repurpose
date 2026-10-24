import { ChevronDown, Cloud, Lock, Monitor, UserCheck } from "lucide-react";

type Where = "local" | "cloud" | "you";

const WHERE: Record<Where, string> = { local: "On this computer", cloud: "Cloud, text only", you: "You decide" };

const STEPS: { title: string; text: string; where: Where }[] = [
  { title: "Transcribe", text: "WhisperX turns the lecture's speech into timed text.", where: "local" },
  { title: "Find clips", text: "A local language model (Ollama) nominates excerpts; rules rank them and enforce length and overlap limits.", where: "local" },
  { title: "Choose clips", text: "Up to three clips are picked automatically, or keep one to five yourself and edit their start and end.", where: "you" },
  { title: "Plan the story", text: "A blueprint fixes the opening, the bridges and the ending before any wording is written.", where: "cloud" },
  { title: "Write the storyboard", text: "The hook, bridge labels and takeaway are written from your selected excerpts only.", where: "cloud" },
  { title: "Build the preview", text: "Kokoro speaks the cards on this computer, illustrations are drawn locally, and FFmpeg adds captions and music.", where: "local" },
  { title: "Approve", text: "A named reviewer watches the preview and approves it. Nothing is exported before that.", where: "you" },
  { title: "Export and check", text: "MP4, captions and post text, with a technical check and a structure check: does the story follow the plan?", where: "local" },
];

export function PipelineGuide() {
  return (
    <details className="guide" open>
      <summary>
        <h2>How Brevio works</h2>
        <ChevronDown size={15} />
      </summary>
      <ol className="guide-steps">
        {STEPS.map((step) => (
          <li key={step.title}>
            <span className={`where where-${step.where}`}>
              {step.where === "cloud" ? <Cloud size={12} /> : step.where === "you" ? <UserCheck size={12} /> : <Monitor size={12} />}
              {WHERE[step.where]}
            </span>
            <strong>{step.title}</strong>
            <p>{step.text}</p>
          </li>
        ))}
      </ol>
    </details>
  );
}

export function PrivacyNotice() {
  return (
    <section className="guide privacy" aria-label="Privacy">
      <h2>
        <Lock size={15} />
        Privacy: what stays here, what is sent
      </h2>
      <div className="privacy-cols">
        <div>
          <span className="where where-local">
            <Monitor size={12} />
            Stays on this computer
          </span>
          <ul>
            <li>The video and its audio</li>
            <li>Every frame, including faces</li>
            <li>The speaker's voice</li>
            <li>Transcription, clip selection, narration and rendering</li>
            <li>All exports</li>
          </ul>
        </div>
        <div>
          <span className="where where-cloud">
            <Cloud size={12} />
            Sent to the cloud text service
          </span>
          <ul>
            <li>The audience you chose and its style notes</li>
            <li>For the clips you keep: titles, takeaways, times and transcript text</li>
            <li>At most five text requests per batch</li>
          </ul>
        </div>
      </div>
      <p className="privacy-never">
        <strong>Never sent:</strong> video, audio or images. There is no face or speaker recognition and no voice cloning; the
        narration voice is synthetic.
      </p>
      <p className="privacy-caveat">
        Transcript text is the speaker's own words. If it contains names or personal details, that text is sent. Use only lectures
        you have the right to reuse, and check the data terms of the cloud text service.
      </p>
    </section>
  );
}

type Profile = { voice: string; opening: string; keeps: string; ending: string; captions: string };

const PROFILES: Record<string, Profile> = {
  Researcher: {
    voice: "Michael, a little slower (0.95x)",
    opening: "A precise research question or counterintuitive finding",
    keeps: "Methods, evidence, assumptions and limitations; nothing invented",
    ending: "The supported conclusion with its key limitation, then one testable follow-up question",
    captions: "Up to 20 characters per second",
  },
  "General public": {
    voice: "Heart, normal speed (1.0x)",
    opening: "A relatable question about everyday stakes such as money, jobs, family or time",
    keeps: "Plain explanations of unfamiliar terms; important caveats kept",
    ending: "One memorable takeaway and one concrete action",
    captions: "Up to 20 characters per second",
  },
  Kids: {
    voice: "Sky, slightly slower with a small pitch lift (an effect, not a child's voice)",
    opening: "A playful \u201cWhat if\u2026?\u201d or \u201cDid you know\u2026?\u201d question",
    keeps: "One concrete idea in short familiar words, safety caveats kept, no baby talk",
    ending: "A big idea in simple words and one tiny safe challenge",
    captions: "Up to 17 characters per second, for ages 8 to 12",
  },
};

export function AudienceGuide({ audience }: { audience: string }) {
  const profile = PROFILES[audience];
  if (!profile) return null;
  return (
    <div className="audience-guide" aria-live="polite">
      <strong>What {audience} gets</strong>
      <dl>
        <div><dt>Voice</dt><dd>{profile.voice}</dd></div>
        <div><dt>Opening</dt><dd>{profile.opening}</dd></div>
        <div><dt>Keeps</dt><dd>{profile.keeps}</dd></div>
        <div><dt>Ending</dt><dd>{profile.ending}</dd></div>
        <div><dt>Captions</dt><dd>{profile.captions}</dd></div>
      </dl>
      <small>Clips are ranked 40% audience fit, 35% completeness and 25% educational value. The Export screen has a rubric to judge the result.</small>
    </div>
  );
}

const DIMENSIONS: [string, string, string][] = [
  ["Source fidelity", "25%", "Claims match the source and its caveats survive."],
  ["Audience fit and learning value", "25%", "Depth and examples suit the audience."],
  ["Standalone coherence", "20%", "Complete ideas, enough setup, a sensible order."],
  ["Captions", "15%", "Accurate, readable and in time with the speech."],
  ["Audio and visuals", "15%", "Clear speech, suitable loudness, legible cards."],
];

export function RubricGuide({ audience }: { audience?: string | null }) {
  const profile = audience ? PROFILES[audience] : undefined;
  return (
    <div className="rubric-guide">
      <p>
        Two automatic checks run on every export. They do not judge facts or audience suitability, so a person then scores the
        content from 0 to 4 on each row, with evidence for every score.
      </p>
      <ul className="rubric-rows">
        {DIMENSIONS.map(([name, weight, look]) => (
          <li key={name}>
            <span>
              <strong>{name}</strong>
              <small>{look}</small>
            </span>
            <em>{weight}</em>
          </li>
        ))}
      </ul>
      <div className="rubric-valid">
        <strong>A valid output</strong>
        <p>
          Every row scores at least 3 of 4, and four gates pass: claims are supported, cuts keep the context, captions are
          readable, and a named person has watched and listened to the final video.
        </p>
      </div>
      {profile && audience && (
        <div className="rubric-audience">
          <strong>For {audience}, also check</strong>
          <ul>
            <li>Opening: {profile.opening.toLowerCase()}</li>
            <li>Ending: {profile.ending.toLowerCase()}</li>
            <li>Captions: {profile.captions.toLowerCase()}</li>
          </ul>
        </div>
      )}
    </div>
  );
}
