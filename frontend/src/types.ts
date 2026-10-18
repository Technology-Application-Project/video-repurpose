export type Stage = "source" | "clips" | "studio" | "export";
export type Options = { audience: string; aspect: string; model: string; whisper_model: string; min_seconds: number; max_seconds: number; source_url: string; automatic: boolean };
export type Clip = {
  id: string;
  title: string;
  takeaway: string;
  start: number;
  end: number;
  excerpt?: string;
  score?: number | null;
  editorial_status?: string;
  editorial_reason?: string;
};
export type Section = {
  kind: string;
  start: number;
  duration: number;
  clip_id?: string;
  title?: string;
  label?: string;
  narration?: { provider?: string; voice?: string };
};
export type Card = {
  title?: string;
  subtitle?: string;
  takeaway?: string;
  question?: string;
  cta?: string;
  label?: string;
  seconds?: number;
};
export type Storyboard = {
  opening?: Card;
  ending?: Card;
  transitions?: Card[];
  evidence?: { clip_id: string; quote: string; start: number; end: number }[];
};
export type Metric = {
  id: string;
  label: string;
  status: string;
  threshold: string;
  value: unknown;
  checks?: string;
  exported?: string;
  fix?: string | null;
};
export type Assessment = {
  metric_version: string;
  technical_status: string;
  counts: Record<string, number>;
  metrics: Metric[];
  publication_status: string;
};
export type ExportItem = {
  id: string;
  title: string;
  audience: string;
  aspect: string;
  created: number;
  duration: number;
  clip_count: number;
  test_export: boolean;
  approved: boolean;
  video: string;
  poster: string | null;
};
export type StructureCheck = {
  version: string;
  status: "passed" | "needs_attention" | "unavailable";
  counts: Record<string, number>;
  metrics: Metric[];
  note: string;
  video_seconds: number | null;
  blueprint: {
    status: string;
    revision: string | null;
    rows: { section: string; planned: string; actual: string; match?: "follows" | "differs" | null }[];
  } | null;
};
export type ExportDetail = ExportItem & {
  folder: string;
  files: string[];
  timeline: Section[];
  clips: Clip[];
  storyboard: Storyboard;
  post: string;
  assessment: Assessment | null;
  structure?: StructureCheck | null;
};
export type Blueprint = {
  version: string;
  audience: string;
  revision: string;
  generated: { status: "model_plan" | "template_plan"; provider?: string; model?: string; note: string };
  clip_count: number;
  clip_count_reason: string;
  clips: { id: string; title: string; start: number; end: number; seconds: number; role: string }[];
  opening: { approach: string; idea: string };
  bridges: { from_clip_id: string; to_clip_id: string; relationship: string; idea: string }[];
  ending: { synthesis: string; action: string };
  timing: {
    speech_seconds: number;
    opening_seconds: number;
    bridge_seconds: number;
    ending_seconds: number;
    estimated_total: number;
    budget: number[];
    fits: boolean;
  };
  risks: string[];
  final?: { clip_count: number; same_clips: boolean; measured_total: number | null; difference?: number };
};
export type Run = {
  options?: Options;
  id: string;
  revision: number;
  filename: string;
  duration: number;
  source: string;
  audience: string | null;
  aspect: string;
  analyzed: boolean;
  confirmed: boolean;
  clips: Clip[];
  selected: string[];
  segments: { start: number; end: number; text: string }[];
  preview: string | null;
  preview_current: boolean;
  approved: boolean;
  reviewer?: string;
  timeline: Section[];
  export_id: string | null;
  storyboard: Storyboard;
  selection_mode: string | null;
  blueprint?: Blueprint | null;
};
export type Job = {
  id: string;
  run: string;
  action: string;
  status: string;
  stage: string;
  created: string;
  message?: string;
  result?: { export_id?: string };
};
export type Source = {
  id: string;
  name: string;
  video: string;
  poster: string;
};
export type Config = {
  token: string;
  audiences: string[];
  estimate: number | null;
  configuration_error: string | null;
  duration: number[];
};
