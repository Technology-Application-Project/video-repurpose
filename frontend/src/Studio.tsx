import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import * as Dialog from "@radix-ui/react-dialog";
import {
  ArrowRight,
  Check,
  CheckCircle2,
  ChevronDown,
  Clapperboard,
  Clock3,
  Copy,
  Download,
  ExternalLink,
  FileText,
  Film,
  FolderOpen,
  History,
  Info,
  Layers3,
  LoaderCircle,
  Monitor,
  Plus,
  RefreshCw,
  Search,
  Settings2,
  ShieldCheck,
  Smartphone,
  Sparkles,
  TriangleAlert,
  Upload,
  X,
} from "lucide-react";
import { api, setToken, upload } from "./api";
import { BlueprintPanel } from "./Blueprint";
import { MetricList, StructurePanel } from "./Checks";
import { structureLabel, technicalLabel } from "./checkLabels";
import { AudienceGuide, PipelineGuide, PrivacyNotice, RubricGuide } from "./Guides";
import type {
  Clip,
  Config,
  ExportDetail,
  ExportItem,
  Job,
  Options,
  Run,
  Source,
  Stage,
} from "./types";
import "./studio.css";

const stages: { id: Stage; label: string }[] = [
  { id: "source", label: "Source" },
  { id: "clips", label: "Clips" },
  { id: "studio", label: "Studio" },
  { id: "export", label: "Export" },
];
const busyJob = (job: Job) => ["queued", "running"].includes(job.status);
const time = (seconds = 0) =>
  `${Math.floor(seconds / 60)
    .toString()
    .padStart(2, "0")}:${Math.floor(seconds % 60)
    .toString()
    .padStart(2, "0")}`;
const key = () => crypto.randomUUID();
const initialLocation = () =>
  new URLSearchParams(window.location.hash.slice(1));
const defaultOptions: Options = { audience: "General public", aspect: "16:9", model: "qwen2.5:latest", whisper_model: "small", min_seconds: 6, max_seconds: 15, source_url: "", automatic: true };

function Studio() {
  const cache = useQueryClient();
  const [stage, setStage] = useState<Stage>(
    stages.find((item) => item.id === initialLocation().get("view"))?.id || "source",
  );
  const [runId, setRunId] = useState(
    initialLocation().get("run") || localStorage.getItem("studio-run") || "",
  );
  const [exportId, setExportId] = useState(
    initialLocation().get("export") || "",
  );
  const [notice, setNotice] = useState("");
  const [optionDraft, setOptionDraft] = useState<{ run: string; value: Options } | null>(null);
  const [sourceId, setSourceId] = useState("");
  const [uploadPercent, setUploadPercent] = useState<number | null>(null);
  const [clipId, setClipId] = useState("");
  const [draft, setDraft] = useState<{ scope: string; clips: Clip[] | null } | null>(null);
  const [search, setSearch] = useState("");
  const [sectionIndex, setSectionIndex] = useState(0);
  const [review, setReview] = useState({ scope: "", reviewer: "", attested: false });
  const [inspectorOpen, setInspectorOpen] = useState(false);
  const [activeTab, setActiveTab] = useState<"summary" | "evidence">("summary");
  const [checkTab, setCheckTab] = useState<"technical" | "structure" | "rubric">("technical");
  const video = useRef<HTMLVideoElement>(null);
  const file = useRef<HTMLInputElement>(null);
  const watchedJob = useRef(localStorage.getItem("studio-job") || "");

  const config = useQuery({
    queryKey: ["config"],
    queryFn: () => api<Config>("/config"),
    staleTime: 60000,
  });
  const sources = useQuery({
    queryKey: ["sources"],
    queryFn: () => api<Source[]>("/sources"),
  });
  const library = useQuery({
    queryKey: ["exports"],
    queryFn: () => api<{ exports: ExportItem[] }>("/exports"),
  });
  const savedRuns = useQuery({
    queryKey: ["runs"],
    queryFn: () => api<Run[]>("/runs"),
  });
  const jobs = useQuery({
    queryKey: ["jobs"],
    queryFn: () => api<Job[]>("/jobs"),
    refetchInterval: (query) => (query.state.data?.some(busyJob) ? 1200 : 5000),
  });
  const runQuery = useQuery({
    queryKey: ["run", runId],
    queryFn: () => api<Run>(`/runs/${runId}`),
    enabled: !!runId,
    refetchInterval: () => (jobs.data?.some((job) => job.run === runId && busyJob(job)) ? 1500 : false),
  });
  const chosenExport = exportId || library.data?.exports[0]?.id || "";
  const exported = useQuery({
    queryKey: ["export", chosenExport],
    queryFn: () => api<ExportDetail>(`/exports/${chosenExport}`),
    enabled: !!chosenExport,
  });
  const mutation = useMutation({
    mutationFn: (execute: () => Promise<void>) => execute(),
    onError: (error) => setNotice(error.message),
  });
  const run = runQuery.data;
  const runBusy = !!jobs.data?.some((job) => job.run === runId && busyJob(job));
  const wasBusy = useRef(false);
  useEffect(() => {
    if (wasBusy.current && !runBusy) void cache.invalidateQueries({ queryKey: ["run", runId] });
    wasBusy.current = runBusy;
  }, [runBusy, runId, cache]);
  const options = optionDraft?.run === runId ? optionDraft.value : run?.options || defaultOptions;
  function setOptions(value: Options) { setOptionDraft({ run: runId, value }); }
  const scope = `${run?.id}:${run?.revision}`;
  const draftClips = draft?.scope === scope ? draft.clips : null;
  const reviewer = review.scope === scope ? review.reviewer : run?.reviewer || "";
  const attested = review.scope === scope && review.attested;
  function setDraftClips(clips: Clip[] | null) { setDraft({ scope, clips }); }
  function setReviewer(value: string) { setReview({ scope, reviewer: value, attested }); }
  function setAttested(value: boolean) { setReview({ scope, reviewer, attested: value }); }
  const latestJob = [...(jobs.data || [])]
    .reverse()
    .find(
      (item) => item.run === runId || item.run === `export:${chosenExport}`,
    );
  const working = mutation.isPending || (!!latestJob && busyJob(latestJob));
  const selectedSource =
    sources.data?.find((item) => item.id === sourceId) || sources.data?.[0];
  const selection =
    draftClips ??
    run?.clips.filter((item) => run.selected.includes(item.id)) ??
    [];
  const currentClip =
    run?.clips.find((item) => item.id === clipId) || run?.clips[0];
  const editedClip =
    selection.find((item) => item.id === currentClip?.id) || currentClip;
  const detail = exported.data;
  const savedStudio = stage === "studio" && !runId;
  const storyboard = savedStudio ? detail?.storyboard : run?.storyboard;
  const timeline = savedStudio ? detail?.timeline || [] : run?.timeline || [];
  const selectedSection = timeline[sectionIndex] || timeline[0];
  const currentCard =
    selectedSection?.kind === "opening"
      ? storyboard?.opening
      : selectedSection?.kind === "ending"
        ? storyboard?.ending
        : selectedSection?.kind === "divider"
          ? storyboard?.transitions?.[
              timeline
                .slice(0, sectionIndex)
                .filter((item) => item.kind === "divider").length
            ]
          : null;
  const sourceClips = savedStudio ? detail?.clips : run?.clips;
  const sectionClip = sourceClips?.find(
    (item) => item.id === selectedSection?.clip_id,
  );

  useEffect(() => {
    if (config.data) setToken(config.data.token);
  }, [config.data]);
  useEffect(() => {
    const hash = new URLSearchParams({ view: stage });
    if (runId) hash.set("run", runId);
    if (exportId) hash.set("export", exportId);
    history.replaceState(null, "", `#${hash}`);
    localStorage.setItem("studio-run", runId);
  }, [stage, runId, exportId]);
  useEffect(() => {
    const job = jobs.data?.find((item) => item.id === watchedJob.current);
    if (!job || busyJob(job)) return;
    watchedJob.current = "";
    localStorage.removeItem("studio-job");
    void cache.invalidateQueries({ queryKey: ["run"] });
    void cache.invalidateQueries({ queryKey: ["runs"] });
    void cache.invalidateQueries({ queryKey: ["exports"] });
    void cache.invalidateQueries({ queryKey: ["export"] });
    if (job.status !== "succeeded") {
      setNotice(
        job.message ||
          "Processing interrupted. Review the run before retrying.",
      );
      return;
    }
    if (job.run === runId && job.action === "analyze" && stage === "source") setStage("clips");
    if (job.run === runId && job.action === "preview" && ["clips", "studio"].includes(stage)) {
      setStage("studio");
      setSectionIndex(0);
    }
    if (job.result?.export_id) {
      setExportId(job.result.export_id);
      if (job.run === runId && job.action === "export") setStage("export");
    }
  }, [jobs.data, cache, stage, runId]);

  function act(execute: () => Promise<void>) {
    setNotice("");
    mutation.mutate(execute);
  }
  function adopt(next: Run) {
    if (next.id !== runId && !next.analyzed) setOptionDraft({ run: next.id, value: options });
    cache.setQueryData(["run", next.id], next);
    setRunId(next.id);
    void cache.invalidateQueries({ queryKey: ["runs"] });
  }
  async function start(action: string, current: Run, extra: object = {}) {
    const job = await api<Job>(`/runs/${current.id}/${action}`, "POST", {
      revision: current.revision,
      key: key(),
      ...extra,
    });
    watchedJob.current = job.id;
    localStorage.setItem("studio-job", job.id);
    void cache.invalidateQueries({ queryKey: ["jobs"] });
  }
  async function chooseFile(selected?: File) {
    if (!selected) return;
    setUploadPercent(0);
    try {
      adopt(await upload<Run>(selected, setUploadPercent));
      setStage("source");
    } finally {
      setUploadPercent(null);
    }
  }
  async function analyze() {
    let current = run;
    if (!current) {
      if (!selectedSource) throw new Error("Upload a source video first.");
      current = await api<Run>(
        `/runs/from-source/${selectedSource.id}`,
        "POST",
      );
      adopt(current);
    }
    await start("analyze", current, { options });
  }
  async function generate() {
    if (!run) return;
    let current = run;
    if (draftClips) {
      current = await api<Run>(`/runs/${run.id}/selection`, "PATCH", {
        revision: run.revision,
        key: key(),
        clips: selection.map(({ id, title, takeaway, start: from, end }) => ({
          id,
          title,
          takeaway: takeaway || "",
          start: from,
          end,
        })),
      });
      adopt(current);
    }
    await start("preview", current);
  }
  function toggle(clip: Clip) {
    if (selection.some((item) => item.id === clip.id))
      setDraftClips(selection.filter((item) => item.id !== clip.id));
    else if (selection.length < 5)
      setDraftClips([...selection, { ...clip, takeaway: clip.takeaway || "" }]);
    else setNotice("Select up to five clips.");
  }
  function editClip(field: keyof Clip, value: string | number) {
    if (!editedClip) return;
    if (!selection.some((item) => item.id === editedClip.id) && selection.length >= 5) {
      setNotice("Uncheck a clip before adding another to the selection.");
      return;
    }
    const edited = { ...editedClip, [field]: value };
    setDraftClips(
      selection.some((item) => item.id === edited.id)
        ? selection.map((item) => (item.id === edited.id ? edited : item))
        : [...selection, edited].slice(0, 5),
    );
  }
  function seek(seconds: number) {
    if (video.current) video.current.currentTime = seconds;
  }
  function openExport(item: ExportItem) {
    setExportId(item.id);
    setStage("export");
    setSectionIndex(0);
  }
  const player = (
    src?: string | null,
    poster?: string | null,
    aspect = "16:9",
  ) => (
    <div className={`screen ${aspect === "9:16" ? "portrait" : ""}`}>
      {src ? (
        <video
          key={src}
          ref={video}
          src={src}
          poster={poster || undefined}
          controls
          playsInline
          preload="metadata"
          aria-label="Video preview"
        />
      ) : (
        <div className="empty-player">
          <Film size={32} />
          <span>
            {working
              ? "Preparing your preview"
              : "Your preview will appear here"}
          </span>
        </div>
      )}
    </div>
  );

  const inspector = (
    <>
      <div className="inspector-heading">
        <span className="eyebrow">INSPECTOR</span>
        <Settings2 size={15} />
      </div>
      <div className="subtabs" role="tablist" aria-label="Inspector">
        <button
          role="tab"
          aria-selected={activeTab === "summary"}
          onClick={() => setActiveTab("summary")}
        >
          Details
        </button>
        <button
          role="tab"
          aria-selected={activeTab === "evidence"}
          onClick={() => setActiveTab("evidence")}
        >
          Source evidence
        </button>
      </div>
      {stage === "clips" && editedClip ? (
        <div className="inspector-body">
          <span className="overline">{editedClip.id} / SOURCE EXCERPT</span>
          <h2>{editedClip.title}</h2>
          {activeTab === "evidence" ? (
            <>
              <blockquote>
                {editedClip.excerpt || "No excerpt recorded."}
              </blockquote>
              <p className="muted">
                {editedClip.editorial_reason ||
                  "Review this passage in the source context."}
              </p>
            </>
          ) : (
            <>
              <label>
                Title
                <input
                  value={editedClip.title}
                  disabled={working}
                  onChange={(event) => editClip("title", event.target.value)}
                />
              </label>
              <label>
                Takeaway
                <textarea
                  value={editedClip.takeaway || ""}
                  disabled={working}
                  onChange={(event) => editClip("takeaway", event.target.value)}
                  rows={3}
                />
              </label>
              <div className="two-fields">
                <label>
                  Start (seconds)
                  <input
                    type="number"
                    min={0}
                    step={0.001}
                    value={editedClip.start}
                    disabled={working}
                    onChange={(event) =>
                      editClip("start", Number(event.target.value))
                    }
                  />
                </label>
                <label>
                  End (seconds)
                  <input
                    type="number"
                    min={0}
                    step={0.001}
                    value={editedClip.end}
                    disabled={working}
                    onChange={(event) =>
                      editClip("end", Number(event.target.value))
                    }
                  />
                </label>
              </div>
              <div className="two-fields">
                <button
                  className="secondary compact"
                  disabled={working}
                  onClick={() =>
                    editClip(
                      "start",
                      Number((video.current?.currentTime || 0).toFixed(3)),
                    )
                  }
                >
                  Set in
                </button>
                <button
                  className="secondary compact"
                  disabled={working}
                  onClick={() =>
                    editClip(
                      "end",
                      Number((video.current?.currentTime || 0).toFixed(3)),
                    )
                  }
                >
                  Set out
                </button>
              </div>
              <div className="info-line">
                <Clock3 size={15} />
                <span>
                  {(editedClip.end - editedClip.start).toFixed(1)} seconds
                  selected
                </span>
              </div>
              <div className="rule" />
              <span className="overline">EDITORIAL ESTIMATE</span>
              <div className="score">
                {typeof editedClip.score === "number"
                  ? `${editedClip.score.toFixed(0)}/100`
                  : "Not scored"}
              </div>
              <p className="muted">
                {editedClip.editorial_status || "Source review required"}
              </p>
            </>
          )}
        </div>
      ) : (
        <div className="inspector-body">
          <span className="overline">
            {selectedSection?.kind || "STORYBOARD"}
          </span>
          <h2>
            {sectionClip?.title ||
              currentCard?.title ||
              currentCard?.label ||
              (selectedSection?.kind === "ending"
                ? "A thought to take away"
                : "The selected section")}
          </h2>
          {activeTab === "evidence" ? (
            storyboard?.evidence?.length ? (
              storyboard.evidence.map((item) => (
                <div className="evidence" key={item.clip_id}>
                  <span className="timecode">
                    {item.clip_id} · {time(item.start)} - {time(item.end)}
                  </span>
                  <blockquote>{item.quote}</blockquote>
                </div>
              ))
            ) : (
              <p className="muted">Source evidence appears after generation.</p>
            )
          ) : (
            <>
              <p className="section-copy">
                {sectionClip?.takeaway ||
                  currentCard?.subtitle ||
                  currentCard?.takeaway ||
                  "Select a section to inspect its wording and timing."}
              </p>
              {currentCard?.question && (
                <p className="muted">{currentCard.question}</p>
              )}
              {currentCard?.cta && (
                <div className="quote-callout">{currentCard.cta}</div>
              )}
              <div className="rule" />
              <div className="definition">
                <span>Duration</span>
                <strong className="timecode">
                  {selectedSection?.duration.toFixed(2) || "0.00"} s
                </strong>
              </div>
              <div className="definition">
                <span>Audience</span>
                <strong>
                  {savedStudio
                    ? detail?.audience
                    : run?.audience || options.audience}
                </strong>
              </div>
              <div className="definition">
                <span>Narration</span>
                  <strong>{timeline.find((item) => item.narration)?.narration?.provider || "Not recorded"}</strong>
              </div>
              <div className="definition">
                <span>Format</span>
                <strong>
                  {savedStudio ? detail?.aspect : run?.aspect || options.aspect}
                </strong>
              </div>
              {savedStudio && (
                <div className="info-line">
                  <ShieldCheck size={16} />
                  <span>Saved export · read-only</span>
                </div>
              )}
            </>
          )}
        </div>
      )}
    </>
  );

  return (
    <div className="app-shell">
      <header className="topbar">
        <a
          className="brand"
          href="#view=source"
          onClick={(event) => {
            event.preventDefault();
            setStage("source");
          }}
        >
          <span className="brand-mark">
            <Clapperboard size={21} />
          </span>
          <strong>
            brevio<span>studio</span>
          </strong>
        </a>
        <span className="header-divider" />
        <span className="project-name">{run?.filename || "Workspace"}</span>
        <div className="top-actions">
          <span className="local-state">
            <i />
            Local workspace
          </span>
          <Dialog.Root>
            <Dialog.Trigger asChild>
              <button
                className="icon-button"
                title="Recent projects"
                aria-label="Recent projects"
              >
                <History size={19} />
              </button>
            </Dialog.Trigger>
            <Dialog.Portal>
              <Dialog.Overlay className="dialog-overlay" />
              <Dialog.Content className="dialog-content">
                <Dialog.Title>Recent projects</Dialog.Title>
                <Dialog.Description className="muted">
                  Saved local source sessions
                </Dialog.Description>
                <Dialog.Close
                  className="dialog-close icon-button"
                  aria-label="Close"
                >
                  <X size={18} />
                </Dialog.Close>
                <div className="recent-list">
                  {savedRuns.data?.length ? (
                    savedRuns.data.map((item) => (
                      <Dialog.Close asChild key={item.id}>
                        <button
                          onClick={() => {
                            setRunId(item.id);
                            setStage(item.analyzed ? "clips" : "source");
                          }}
                        >
                          <Film size={20} />
                          <span>
                            <strong>{item.filename}</strong>
                            <small>
                              {item.audience || "Source ready"} ·{" "}
                              {time(item.duration)}
                            </small>
                          </span>
                          <ArrowRight size={17} />
                        </button>
                      </Dialog.Close>
                    ))
                  ) : (
                    <p className="muted">No saved projects yet.</p>
                  )}
                </div>
              </Dialog.Content>
            </Dialog.Portal>
          </Dialog.Root>
          <button
            className="secondary new-project"
            title="New project"
            aria-label="New project"
            onClick={() => {
              setRunId("");
              setDraftClips(null);
              setStage("source");
              setNotice("");
            }}
            disabled={mutation.isPending}
          >
            <Plus size={16} />
            <span>New project</span>
          </button>
        </div>
      </header>
      <nav className="workflow-nav" aria-label="Workflow">
        <div className="steps">
          {stages.map((item, index) => (
            <button
              key={item.id}
              className={stage === item.id ? "active" : ""}
              aria-current={stage === item.id ? "step" : undefined}
              onClick={() => setStage(item.id)}
            >
              <span className="step-number">{index + 1}</span>
              {item.label}
              {index < 3 && <span className="step-line" />}
            </button>
          ))}
        </div>
        <span className="nav-note">
          <span className="lime-dot" />
          30-60 second stories
        </span>
      </nav>
      {(notice || config.error || runQuery.error) && (
        <div className="notice" role="alert">
          <TriangleAlert size={18} />
          <span>
            {notice || config.error?.message || runQuery.error?.message}
          </span>
          <button
            className="icon-button"
            aria-label="Dismiss message"
            onClick={() => {
              setNotice("");
              if (runQuery.error) setRunId("");
            }}
          >
            <X size={16} />
          </button>
        </div>
      )}
      {latestJob && busyJob(latestJob) && (
        <div className="job-strip" role="status">
          <LoaderCircle className="spin" size={18} />
          <strong>{latestJob.stage}</strong>
          <span>
            {latestJob.status === "queued"
              ? "Waiting for the local worker"
              : "Processing on this workspace"}
          </span>
        </div>
      )}
      <main>
        {stage === "source" && (
          <div className="source-workspace workspace-enter">
            <div className="page-heading">
              <div>
                <span className="eyebrow">
                  START WITH SOMETHING WORTH SHARING
                </span>
                <h1>One lecture. A new perspective.</h1>
              </div>
              <span className="step-caption">01 / SOURCE</span>
            </div>
            <div className="source-grid">
              <section className="source-main">
                <div className="section-header">
                  <h2>
                    <Film size={17} />
                    Source footage
                  </h2>
                  <button
                    className="text-button"
                    onClick={() => file.current?.click()}
                    disabled={working}
                  >
                    <Upload size={15} />
                    {run ? "Replace video" : "Upload video"}
                  </button>
                </div>
                <input
                  ref={file}
                  type="file"
                  accept="video/*"
                  className="sr-only"
                  aria-label="Upload source video"
                  onChange={(event) =>
                    act(() => chooseFile(event.target.files?.[0]))
                  }
                />
                <div
                  className="source-view"
                  onDragOver={(event) => event.preventDefault()}
                  onDrop={(event) => {
                    event.preventDefault();
                    if (!working)
                      act(() => chooseFile(event.dataTransfer.files[0]));
                  }}
                >
                  {player(
                    run?.source || selectedSource?.video,
                    !run ? selectedSource?.poster : undefined,
                  )}
                  {!run && !selectedSource && (
                    <button
                      className="upload-empty"
                      onClick={() => file.current?.click()}
                    >
                      <Upload size={24} />
                      Choose a lecture video
                    </button>
                  )}
                </div>
                <div className="source-meta">
                  <span>
                    <i className="lime-dot" />
                    {run?.filename ||
                      selectedSource?.name ||
                      "No source selected"}
                  </span>
                  <span className="timecode">
                    {run ? time(run.duration) : "LOCAL VIDEO"}
                  </span>
                </div>
                {uploadPercent !== null && (
                  <div className="upload-progress">
                    <progress max={100} value={uploadPercent} />
                    <span>{uploadPercent}% uploaded</span>
                  </div>
                )}
                {!!sources.data?.length && (
                  <div className="source-library">
                    <label>
                      Available on this Mac
                      <select
                        value={selectedSource?.id || ""}
                        disabled={working}
                        onChange={(event) => {
                          setSourceId(event.target.value);
                          setRunId("");
                        }}
                      >
                        {sources.data.map((item) => (
                          <option key={item.id} value={item.id}>
                            {item.name}
                          </option>
                        ))}
                      </select>
                    </label>
                  </div>
                )}
              </section>
              <aside className="source-settings">
                <span className="eyebrow">MAKE IT RELEVANT</span>
                <h2>Choose your audience</h2>
                <div
                  className="audience-options"
                  role="radiogroup"
                  aria-label="Target audience"
                >
                  {["Researcher", "General public", "Kids"].map(
                    (audience, index) => (
                      <button
                        key={audience}
                        role="radio"
                        aria-checked={options.audience === audience}
                        disabled={working}
                        className={
                          options.audience === audience ? "selected" : ""
                        }
                        onClick={() => setOptions({ ...options, audience })}
                      >
                        <span className={`audience-icon audience-${index}`}>
                          {index === 0 ? (
                            <Search size={18} />
                          ) : index === 1 ? (
                            <Layers3 size={18} />
                          ) : (
                            <Sparkles size={18} />
                          )}
                        </span>
                        <span>
                          <strong>{audience}</strong>
                          <small>
                            {
                              [
                                "Evidence & ideas",
                                "Clear & relatable",
                                "Curious minds, ages 8-12",
                              ][index]
                            }
                          </small>
                        </span>
                        <span className="radio-mark">
                          {options.audience === audience && <span />}
                        </span>
                      </button>
                    ),
                  )}
                </div>
                <AudienceGuide audience={options.audience} />
                <div className="rule" />
                <label>Output format</label>
                <div
                  className="format-options"
                  role="radiogroup"
                  aria-label="Output format"
                >
                  {["16:9", "9:16"].map((aspect) => (
                    <button
                      role="radio"
                      aria-checked={options.aspect === aspect}
                      className={options.aspect === aspect ? "selected" : ""}
                      key={aspect}
                      disabled={working}
                      onClick={() => setOptions({ ...options, aspect })}
                    >
                      {aspect === "16:9" ? (
                        <Monitor size={20} />
                      ) : (
                        <Smartphone size={20} />
                      )}
                      <span>
                        {aspect === "16:9" ? "Landscape" : "Portrait"}
                        <small>{aspect}</small>
                      </span>
                      {options.aspect === aspect && <Check size={14} />}
                    </button>
                  ))}
                </div>
                <label className="check-row">
                  <input
                    type="checkbox"
                    checked={options.automatic}
                    disabled={working}
                    onChange={(event) =>
                      setOptions({
                        ...options,
                        automatic: event.target.checked,
                      })
                    }
                  />
                  Select clips automatically
                </label>
                <details className="advanced">
                  <summary>
                    <Settings2 size={15} />
                    Advanced settings
                    <ChevronDown size={15} />
                  </summary>
                  <label>Aspect ratio<select disabled={working} value={options.aspect} onChange={(event) => setOptions({ ...options, aspect: event.target.value })}><option value="16:9">Landscape · 16:9</option><option value="9:16">Portrait · 9:16</option><option value="Original">Original source ratio</option><option value="1:1">Square · 1:1</option></select></label>
                  <label>
                    Transcription quality
                    <select
                      value={options.whisper_model}
                      onChange={(event) =>
                        setOptions({
                          ...options,
                          whisper_model: event.target.value,
                        })
                      }
                    >
                      <option value="small">Fast · small</option>
                      <option value="distil-large-v3">
                        Balanced · distil-large-v3
                      </option>
                      <option value="large-v3">Detailed · large-v3</option>
                    </select>
                  </label>
                  <label>
                    Local model
                    <input
                      value={options.model}
                      onChange={(event) =>
                        setOptions({ ...options, model: event.target.value })
                      }
                    />
                  </label>
                  <div className="two-fields">
                    <label>
                      Min clip (s)
                      <input
                        type="number"
                        min={3}
                        max={20}
                        value={options.min_seconds}
                        onChange={(event) =>
                          setOptions({
                            ...options,
                            min_seconds: Number(event.target.value),
                          })
                        }
                      />
                    </label>
                    <label>
                      Max clip (s)
                      <input
                        type="number"
                        min={5}
                        max={45}
                        value={options.max_seconds}
                        onChange={(event) =>
                          setOptions({
                            ...options,
                            max_seconds: Number(event.target.value),
                          })
                        }
                      />
                    </label>
                  </div>
                  <label>
                    Source attribution URL
                    <input
                      type="url"
                      value={options.source_url}
                      onChange={(event) =>
                        setOptions({
                          ...options,
                          source_url: event.target.value,
                        })
                      }
                    />
                    <small className="field-hint">
                      Left empty, the ending card shows the placeholder example.edu/lecture.
                    </small>
                  </label>
                </details>
                <button
                  className="primary wide"
                  onClick={() => act(analyze)}
                  disabled={
                    working || !config.data || (!run && !selectedSource)
                  }
                >
                  {working ? (
                    <LoaderCircle className="spin" size={17} />
                  ) : (
                    <Sparkles size={17} />
                  )}
                  Analyze source
                  <ArrowRight size={17} />
                </button>
                <div className="quiet-meta">
                  <ShieldCheck size={13} />
                  Local transcription & clip selection
                </div>
              </aside>
            </div>
            <section className="guide-grid">
              <PipelineGuide />
              <PrivacyNotice />
            </section>
            <section className="recent-section">
              <div className="section-header">
                <h2>
                  Recent exports{" "}
                  <span className="count">
                    {library.data?.exports.length || 0}
                  </span>
                </h2>
                <button
                  className="text-button"
                  onClick={() => setStage("export")}
                >
                  View library
                  <ArrowRight size={15} />
                </button>
              </div>
              <div className="export-grid">
                {library.data?.exports.slice(0, 3).map((item) => (
                  <ExportCard
                    key={item.id}
                    item={item}
                    onClick={() => openExport(item)}
                  />
                ))}
                {library.isPending && (
                  <p className="muted">Loading local exports...</p>
                )}
                {library.isError && (
                  <p className="muted">
                    Could not load exports. Check the local API.
                  </p>
                )}
              </div>
            </section>
          </div>
        )}
        {stage === "clips" &&
          (!run?.analyzed ? (
            <Empty
              title="Find your best moments"
              message="Analyze a source to see its suggested excerpts."
              button="Choose source"
              onClick={() => setStage("source")}
            />
          ) : (
            <div className="editing-workspace workspace-enter">
              <div className="editor-toolbar">
                <div>
                  <h1>Shape the story</h1>
                  <span>
                    {run.audience} <span className="separator">/</span>{" "}
                    {selection.length} clips selected
                  </span>
                </div>
                <div className="toolbar-actions">
                  <span className="timecode">
                    {selection
                      .reduce((sum, clip) => sum + clip.end - clip.start, 0)
                      .toFixed(1)}
                    s of source
                  </span>
                  <button
                    className="primary"
                    disabled={
                      working ||
                      !selection.length ||
                      !config.data ||
                      selection.some((item) => item.end <= item.start)
                    }
                    onClick={() => act(generate)}
                  >
                    <Sparkles size={16} />
                    Generate preview
                    <ArrowRight size={16} />
                  </button>
                </div>
              </div>
              <BlueprintPanel
                plan={run.blueprint}
                planning={working && latestJob?.action === "preview"}
              />
              <div className="editor-grid">
                <aside className="clip-browser">
                  <div className="section-header">
                    <h2>Excerpts</h2>
                    <span className="count">{run.clips.length}</span>
                  </div>
                  <label className="search-field">
                    <Search size={15} />
                    <input
                      aria-label="Search clips"
                      placeholder="Find a moment"
                      value={search}
                      onChange={(event) => setSearch(event.target.value)}
                    />
                  </label>
                  <div className="clip-list">
                    {run.clips
                      .filter((item) =>
                        `${item.title} ${item.excerpt}`
                          .toLowerCase()
                          .includes(search.toLowerCase()),
                      )
                      .map((item, index) => (
                        <div
                          className={`clip-row ${currentClip?.id === item.id ? "active" : ""}`}
                          key={item.id}
                        >
                          <button
                            className="clip-select"
                            onClick={() => {
                              setClipId(item.id);
                              seek(item.start);
                            }}
                          >
                            <span className="clip-index">
                              {String(index + 1).padStart(2, "0")}
                            </span>
                            <span>
                              <strong>{item.title}</strong>
                              <small className="timecode">
                                {time(item.start)} - {time(item.end)}
                              </small>
                            </span>
                          </button>
                          <input
                            type="checkbox"
                            aria-label={`Keep ${item.title}`}
                            checked={selection.some(
                              (clip) => clip.id === item.id,
                            )}
                            disabled={working}
                            onChange={() => toggle(item)}
                          />
                        </div>
                      ))}
                  </div>
                  <div className="browser-footer">
                    <span className="lime-dot" />
                    {draftClips
                      ? "Manual selection"
                      : run.selection_mode === "automatic"
                        ? "Automatically selected"
                        : "Manual selection"}
                  </div>
                </aside>
                <section className="editor-canvas">
                  <div className="section-header">
                    <h2>{currentClip?.title || "Source preview"}</h2>
                    <span className="timecode">SOURCE</span>
                  </div>
                  {player(run.source)}
                  <div className="under-player">
                    <span className="timecode">
                      {time(currentClip?.start)} - {time(currentClip?.end)}
                    </span>
                    <button
                      className="text-button mobile-inspect"
                      onClick={() => setInspectorOpen(true)}
                    >
                      <Settings2 size={15} />
                      Inspect clip
                    </button>
                  </div>
                  <details className="transcript">
                    <summary>
                      <FileText size={15} />
                      Source transcript
                      <ChevronDown size={15} />
                    </summary>
                    <div>
                      {run.segments.map((segment, index) => (
                        <button key={index} onClick={() => seek(segment.start)}>
                          <span className="timecode">
                            {time(segment.start)}
                          </span>
                          <span>{segment.text}</span>
                        </button>
                      ))}
                    </div>
                  </details>
                </section>
                <aside className="inspector">{inspector}</aside>
              </div>
              <div className="bottom-note">
                <Info size={14} />
                <span>
                  Generation uses a new cloud batch
                  {config.data?.estimate != null
                    ? ` · estimate $${config.data.estimate.toFixed(2)}`
                    : ""}
                  . Preview approval is required before export.
                </span>
              </div>
            </div>
          ))}
        {stage === "studio" && (
          <div className="editing-workspace workspace-enter">
            <div className="editor-toolbar">
              <div>
                <span className="eyebrow">THE EDIT</span>
                <h1>
                  {storyboard?.opening?.title || "Bring the story together"}
                </h1>
              </div>
              <div className="toolbar-actions">
                {run?.preview && (
                  <button
                    className="secondary"
                    disabled={working}
                    onClick={() => act(generate)}
                    title="Starts a new cloud generation batch"
                  >
                    <RefreshCw size={15} />
                    Regenerate
                  </button>
                )}
                <span
                  className={`status ${run?.approved && !savedStudio ? "status-success" : ""}`}
                >
                  {savedStudio
                    ? "Saved export"
                    : run?.approved
                      ? "Approved"
                      : run?.preview_current
                        ? "Ready for review"
                        : "Preview required"}
                </span>
              </div>
            </div>
            {!savedStudio && run?.blueprint && (
              <details className="blueprint-details">
                <summary>
                  Blueprint
                  <ChevronDown size={15} />
                </summary>
                <BlueprintPanel plan={run.blueprint} planning={false} />
              </details>
            )}
            <div className="studio-grid">
              <section className="studio-canvas">
                {player(
                  savedStudio ? detail?.video : run?.preview,
                  savedStudio ? detail?.poster : undefined,
                  savedStudio ? detail?.aspect : run?.aspect,
                )}
                <div className="under-player">
                  <span>
                    <span className="lime-dot" />
                    {savedStudio
                      ? detail?.audience
                      : run?.audience || "No active preview"}
                  </span>
                  <span className="timecode">
                    {timeline
                      .reduce((sum, item) => sum + item.duration, 0)
                      .toFixed(1)}
                    s <span className="muted">/ 30-60s</span>
                  </span>
                  <button
                    className="text-button mobile-inspect"
                    onClick={() => setInspectorOpen(true)}
                  >
                    <Settings2 size={15} />
                    Inspector
                  </button>
                </div>
                <div className="timeline-heading">
                  <span className="eyebrow">STORY SEQUENCE</span>
                  <span className="timecode">{timeline.length} sections</span>
                </div>
                <div className="timeline" aria-label="Story section navigator">
                  {timeline.map((item, index) => (
                    <button
                      key={`${item.start}-${index}`}
                      style={{ flexGrow: Math.max(item.duration, 2) }}
                      className={`timeline-section section-${item.kind} ${sectionIndex === index ? "active" : ""}`}
                      onClick={() => {
                        setSectionIndex(index);
                        seek(item.start);
                      }}
                      aria-label={`${item.kind} at ${time(item.start)}`}
                      aria-pressed={sectionIndex === index}
                    >
                      <span>
                        {item.kind === "divider"
                          ? "Bridge"
                          : item.kind === "clip"
                            ? item.clip_id
                            : item.kind}
                      </span>
                      <small>{item.duration.toFixed(1)}s</small>
                    </button>
                  ))}
                  {!timeline.length && (
                    <span className="muted">No rendered sequence yet.</span>
                  )}
                </div>
                <div className="timeline-ruler">
                  <span>00:00</span>
                  <span className="timecode">
                    {time(
                      timeline.reduce((sum, item) => sum + item.duration, 0),
                    )}
                  </span>
                </div>
                {savedStudio ? (
                  <div className="approval-bar">
                    <ShieldCheck size={21} />
                    <div>
                      <strong>
                        {detail?.approved
                          ? "Preview approved at export"
                          : "Automated test export"}
                      </strong>
                      <span>Publication suitability is not assessed.</span>
                    </div>
                    <button
                      className="primary"
                      onClick={() => setStage("export")}
                    >
                      <Download size={16} />
                      View export
                    </button>
                  </div>
                ) : run?.preview_current ? (
                  <div className="approval-section">
                    <div className="approval-heading">
                      <ShieldCheck size={19} />
                      <div>
                        <h2>
                          {run.approved
                            ? "Ready for export"
                            : "A final human check"}
                        </h2>
                        <span>
                          Review the preview, source context and text placement.
                        </span>
                      </div>
                    </div>
                    {!run.approved && (
                      <>
                        <label>
                          Reviewer name
                          <input
                            value={reviewer}
                            disabled={working}
                            onChange={(event) =>
                              setReviewer(event.target.value)
                            }
                            placeholder="Your name"
                          />
                        </label>
                        <label className="check-row">
                          <input
                            type="checkbox"
                            checked={attested}
                            disabled={working}
                            onChange={(event) =>
                              setAttested(event.target.checked)
                            }
                          />
                          I reviewed the preview and source evidence. Claims and
                          joins are supported.
                        </label>
                      </>
                    )}
                    <div className="approval-actions">
                      <span className="muted">
                        {run.approved
                          ? `Approved by ${run.reviewer}`
                          : "Approval applies to this exact preview."}
                      </span>
                      <button
                        className="primary"
                        disabled={
                          working ||
                          (!run.approved && (!attested || !reviewer.trim()))
                        }
                        onClick={() =>
                          act(async () => {
                            if (run.approved) await start("exports", run);
                            else
                              adopt(
                                await api<Run>(
                                  `/runs/${run.id}/approval`,
                                  "POST",
                                  {
                                    revision: run.revision,
                                    key: key(),
                                    reviewer,
                                    attested,
                                  },
                                ),
                              );
                          })
                        }
                      >
                        {run.approved ? (
                          <Download size={16} />
                        ) : (
                          <ShieldCheck size={16} />
                        )}
                        {run.approved ? "Export video" : "Approve preview"}
                        <ArrowRight size={16} />
                      </button>
                    </div>
                  </div>
                ) : (
                  <div className="approval-bar">
                    <Info size={20} />
                    <div>
                      <strong>
                        {run?.preview
                          ? "This preview needs renewed review"
                          : "Select your clips first"}
                      </strong>
                      <span>Generate a current preview to continue.</span>
                    </div>
                    <button
                      className="secondary"
                      onClick={() =>
                        setStage(run?.analyzed ? "clips" : "source")
                      }
                    >
                      Back to {run?.analyzed ? "clips" : "source"}
                      <ArrowRight size={15} />
                    </button>
                  </div>
                )}
              </section>
              <aside className="inspector">{inspector}</aside>
            </div>
          </div>
        )}
        {stage === "export" && (
          <div className="export-workspace workspace-enter">
            <div className="page-heading">
              <div>
                <span className="eyebrow">YOUR FINISHED STORIES</span>
                <h1>Export library</h1>
              </div>
              <button
                className="secondary"
                onClick={() => {
                  void cache.invalidateQueries({ queryKey: ["exports"] });
                  void cache.invalidateQueries({ queryKey: ["export"] });
                }}
              >
                <RefreshCw size={15} />
                Refresh
              </button>
            </div>
            <div className="export-layout">
              <aside className="export-browser">
                <div className="section-header">
                  <h2>All exports</h2>
                  <span className="count">
                    {library.data?.exports.length || 0}
                  </span>
                </div>
                <div className="export-list">
                  {library.data?.exports.map((item) => (
                    <button
                      className={chosenExport === item.id ? "active" : ""}
                      key={item.id}
                      onClick={() => setExportId(item.id)}
                    >
                      {item.poster ? (
                        <img src={item.poster} alt="" loading="lazy" />
                      ) : (
                        <span className="thumbnail-empty">
                          <Film size={22} />
                        </span>
                      )}
                      <span>
                        <strong>{item.title}</strong>
                        <small>
                          {item.audience} · {item.duration.toFixed(1)}s
                        </small>
                        <small>
                          {new Date(item.created * 1000).toLocaleDateString(
                            undefined,
                            { month: "short", day: "numeric" },
                          )}
                        </small>
                      </span>
                    </button>
                  ))}
                </div>
              </aside>
              {detail ? (
                <section className="export-detail">
                  <div className="export-title">
                    <span className="audience-tag">{detail.audience}</span>
                    <span className="timecode">
                      {detail.aspect} / {detail.duration.toFixed(1)}s
                    </span>
                  </div>
                  <h2>{detail.title}</h2>
                  {player(detail.video, detail.poster, detail.aspect)}
                  <div className="download-bar">
                    <a
                      className="primary"
                      href={`${detail.video}?download=true`}
                    >
                      <Download size={16} />
                      Download MP4
                    </a>
                    {detail.files.find((name) => name.endsWith(".zip")) && (
                      <a
                        className="secondary"
                        href={`/api/exports/${detail.id}/files/${detail.files.find((name) => name.endsWith(".zip"))}?download=true`}
                      >
                        <Download size={16} />
                        ZIP package
                      </a>
                    )}
                    <button
                      className="icon-button"
                      title="Reveal in Finder"
                      aria-label="Reveal in Finder"
                      onClick={() =>
                        act(async () => {
                          await api(`/exports/${detail.id}/reveal`, "POST");
                        })
                      }
                    >
                      <FolderOpen size={20} />
                    </button>
                    <button
                      className="icon-button"
                      title="Open in studio"
                      aria-label="Open in studio"
                      onClick={() => {
                        setRunId("");
                        setStage("studio");
                        setSectionIndex(0);
                      }}
                    >
                      <ExternalLink size={18} />
                    </button>
                  </div>
                  <div className="file-links">
                    {detail.files
                      .filter(
                        (name) =>
                          !name.endsWith(".mp4") && !name.endsWith(".zip"),
                      )
                      .map((name) => (
                        <a
                          key={name}
                          href={`/api/exports/${detail.id}/files/${name}?download=true`}
                        >
                          <FileText size={14} />
                          {name}
                        </a>
                      ))}
                    <button
                      className="text-button"
                      onClick={() =>
                        act(async () => {
                          await navigator.clipboard.writeText(detail.folder);
                          setNotice("Output folder copied.");
                        })
                      }
                    >
                      <Copy size={14} />
                      Copy folder path
                    </button>
                  </div>
                  <details className="post-copy">
                    <summary>
                      Post copy
                      <ChevronDown size={15} />
                    </summary>
                    <p>{detail.post}</p>
                    <button
                      className="secondary compact"
                      onClick={() =>
                        act(async () => {
                          await navigator.clipboard.writeText(detail.post);
                          setNotice("Post copied.");
                        })
                      }
                    >
                      <Copy size={14} />
                      Copy post
                    </button>
                  </details>
                </section>
              ) : (
                <Empty
                  title="Your exports will live here"
                  message="A reviewed preview becomes a versioned video and download package."
                  button="Choose source"
                  onClick={() => setStage("source")}
                />
              )}
              {detail && (
                <aside className="assessment-panel">
                  <div className="section-header">
                    <h2>
                      <ShieldCheck size={17} />
                      Export checks
                    </h2>
                  </div>
                  <div className="check-tabs" role="tablist" aria-label="Export checks">
                    {(
                      [
                        ["technical", "Technical check", technicalLabel(detail.assessment)],
                        ["structure", "Structure check", structureLabel(detail.structure)],
                        ["rubric", "How to judge", "5 criteria"],
                      ] as const
                    ).map(([id, label, state]) => (
                      <button
                        key={id}
                        role="tab"
                        aria-selected={checkTab === id}
                        className={checkTab === id ? "active" : ""}
                        onClick={() => setCheckTab(id)}
                      >
                        {label}
                        <small>{state}</small>
                      </button>
                    ))}
                  </div>
                  {checkTab === "structure" ? (
                    <StructurePanel structure={detail.structure} />
                  ) : checkTab === "rubric" ? (
                    <RubricGuide audience={detail.audience} />
                  ) : detail.assessment ? (
                    <>
                      <div
                        className={`assessment-summary ${detail.assessment.technical_status === "passed" ? "passed" : ""}`}
                      >
                        {detail.assessment.technical_status === "passed" ? (
                          <CheckCircle2 size={23} />
                        ) : (
                          <TriangleAlert size={23} />
                        )}
                        <div>
                          <strong>
                            {detail.assessment.technical_status === "passed"
                              ? "Checks passed"
                              : "Needs attention"}
                          </strong>
                          <span>
                            {detail.assessment.counts.pass} passed ·{" "}
                            {detail.assessment.counts.fail} failed
                          </span>
                        </div>
                      </div>
                      <MetricList metrics={detail.assessment.metrics} />
                    </>
                  ) : (
                    <p className="muted">No technical report yet.</p>
                  )}
                  <button
                    className="secondary wide"
                    disabled={working}
                    onClick={() =>
                      act(async () => {
                        const job = await api<Job>(
                          `/exports/${detail.id}/assessment`,
                          "POST",
                          { revision: 0, key: key() },
                        );
                        watchedJob.current = job.id;
                        localStorage.setItem("studio-job", job.id);
                        void cache.invalidateQueries({ queryKey: ["jobs"] });
                      })
                    }
                  >
                    <RefreshCw size={15} />
                    Run technical check
                  </button>
                  <div className="publication-note">
                    <ShieldCheck size={17} />
                    <div>
                      <strong>Publication not assessed</strong>
                      <p>
                        {detail.test_export ? "Automated test export. " : ""}
                        Technical checks do not replace human content review.
                      </p>
                    </div>
                  </div>
                </aside>
              )}
            </div>
          </div>
        )}
      </main>
      <footer className="app-footer">
        <span>
          <span className="lime-dot" />
          BREVIO STUDIO
        </span>
        <span>Local processing. Considered stories.</span>
        <span>
          {config.data?.estimate != null
            ? `$${config.data.estimate.toFixed(2)} / new generation batch`
            : "Provider configuration required"}
        </span>
      </footer>
      <Dialog.Root open={inspectorOpen} onOpenChange={setInspectorOpen}>
        <Dialog.Portal>
          <Dialog.Overlay className="dialog-overlay" />
          <Dialog.Content className="inspector-drawer">
            <Dialog.Title className="sr-only">Section inspector</Dialog.Title>
            <Dialog.Description className="sr-only">
              Details and source evidence for the selected section.
            </Dialog.Description>
            <Dialog.Close
              className="dialog-close icon-button"
              aria-label="Close inspector"
            >
              <X size={18} />
            </Dialog.Close>
            {inspector}
          </Dialog.Content>
        </Dialog.Portal>
      </Dialog.Root>
    </div>
  );
}

function ExportCard({
  item,
  onClick,
}: {
  item: ExportItem;
  onClick: () => void;
}) {
  return (
    <button className="export-card" onClick={onClick}>
      <div className="export-card-image">
        {item.poster ? (
          <img src={item.poster} alt="" loading="lazy" />
        ) : (
          <Film size={30} />
        )}
        <span className="duration-badge">{time(item.duration)}</span>
        <span className="export-card-arrow">
          <ArrowRight size={17} />
        </span>
      </div>
      <div className="export-card-copy">
        <span
          className={`audience-label audience-${item.audience === "Researcher" ? "researcher" : item.audience === "Kids" ? "kids" : "public"}`}
        >
          {item.audience}
        </span>
        <strong>{item.title}</strong>
        {item.test_export && <span className="test-label">Test export</span>}
        <span>
          {item.clip_count} clips <span className="separator">/</span>{" "}
          {new Date(item.created * 1000).toLocaleDateString(undefined, {
            month: "short",
            day: "numeric",
          })}
        </span>
      </div>
    </button>
  );
}
function Empty({
  title,
  message,
  button,
  onClick,
}: {
  title: string;
  message: string;
  button: string;
  onClick: () => void;
}) {
  return (
    <section className="empty-state">
      <Layers3 size={34} />
      <h1>{title}</h1>
      <p>{message}</p>
      <button className="primary" onClick={onClick}>
        {button}
        <ArrowRight size={16} />
      </button>
    </section>
  );
}
export default Studio;
