# Educational Short Clip Demo

Gradio app that turns a long lecture video into a short compilation: transcribe → Ollama nominates clips → embedding rank → human keep 3–5 → ffmpeg assemble with captions.

Code walkthrough: [`pipeline/README.md`](pipeline/README.md). Product spec: [`gradio_demo_workflow_plan.md`](gradio_demo_workflow_plan.md).

## What is in this repo

```text
app.py                      Gradio UI
pipeline/                   transcribe, nominate, rank, cut, assemble, export
prompts/nominate_clips.txt  Ollama system prompt
docs/                       architecture notes
requirements.txt
```

`work/` and `exports/` are local run output (source copies, previews, `compilation.mp4`). They are gitignored and should not be pushed.

## Requirements

- Python 3.11+
- [ffmpeg](https://ffmpeg.org/) on `PATH`
- [Ollama](https://ollama.com/) running locally, with a chat model (default `qwen2.5`)

```bash
ollama pull qwen2.5
pip install -r requirements.txt
```

WhisperX will pull PyTorch and ASR weights on first transcribe.

## Run

```bash
python app.py
```

1. Upload a local lecture mp4, set topic and 3–5 learning objectives.
2. Transcribe, then **Nominate + rank clips**.
3. On Review, keep 3–5 on-topic clips (edit start/end if needed).
4. Export one compilation (intro + clips + captions + takeaway/CTA).

Do not commit lecture recordings unless you have rights to redistribute them.
