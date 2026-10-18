import copy
import json
import os
import secrets
import subprocess
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool
from starlette.middleware.trustedhost import TrustedHostMiddleware

from api.catalog import Catalog
from api.jobs import Jobs
from api.schemas import Action, Analyze, Approval, Selection
from api.studio import Studio
from pipeline import workflow
from pipeline.project import data_hash


ROOT = Path(__file__).resolve().parents[1]


def create_app(root=ROOT):
    from dotenv import load_dotenv
    load_dotenv(Path(root) / ".env", override=False)

    @asynccontextmanager
    async def lifespan(server):
        yield
        await run_in_threadpool(server.state.jobs.executor.shutdown, wait=True)

    server = FastAPI(title="Brevio Studio", docs_url=None, redoc_url=None, lifespan=lifespan)
    server.add_middleware(TrustedHostMiddleware, allowed_hosts=["localhost", "127.0.0.1"])
    catalog = Catalog(Path(root))
    studio = Studio(Path(root) / "work" / "studio")
    jobs = Jobs(studio.directory / "jobs")
    token = secrets.token_urlsafe(32)
    server.state.catalog = catalog
    server.state.studio = studio
    server.state.jobs = jobs
    origins = {"http://127.0.0.1:5173", "http://localhost:5173"}

    @server.middleware("http")
    async def local_boundary(request: Request, call_next):
        origin = request.headers.get("origin")
        if origin and origin not in origins | {str(request.base_url).rstrip("/")}:
            return JSONResponse({"detail": "Untrusted origin"}, status_code=403)
        if request.method not in ("GET", "HEAD", "OPTIONS") and not secrets.compare_digest(request.headers.get("x-studio-token", ""), token):
            return JSONResponse({"detail": "Refresh the studio before continuing."}, status_code=403)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "same-origin"
        if request.url.path.startswith("/api/") and "media" not in request.url.path and "/files/" not in request.url.path:
            response.headers["Cache-Control"] = "no-store"
        return response

    def record(identity):
        try:
            return studio.get(identity)
        except KeyError as error:
            raise HTTPException(404, "Run not found") from error

    def available(identity, revision):
        current = record(identity)
        if any(item["run"] == identity and item["status"] in ("queued", "running") for item in jobs.items.values()):
            raise HTTPException(409, "This run is processing. Wait for it to finish before editing.")
        try:
            studio.validate(current, revision)
        except ValueError as error:
            raise HTTPException(409, str(error)) from error
        return current

    def submit(identity, action, body, execute):
        signature = data_hash(body.model_dump())
        with studio.lock, jobs.lock:
            prior = next((item for item in jobs.items.values() if item["run"] == identity and item["key"] == body.key), None)
            if prior:
                if prior["action"] != action or prior.get("signature") != signature:
                    raise HTTPException(409, "Request key already used for different input.")
                return dict(prior)
            current = available(identity, body.revision)
            return jobs.submit(identity, action, body.key, lambda progress: execute(current, progress), signature)

    def sources():
        paths = [*Path(root).glob("downloads/*.mp4"), *Path(root).glob("source.mp4")]
        return {data_hash(str(path.relative_to(root)))[:20]: path for path in paths if path.resolve().is_relative_to(Path(root).resolve())}

    def source(identity):
        path = sources().get(identity)
        if path is None:
            raise HTTPException(404, "Source not found")
        return path

    @server.get("/api/config")
    def config():
        from pipeline.cloud import cloud_quote
        from pipeline.storyboard import AUDIENCE_PROFILES
        try:
            quote = cloud_quote()
            estimate, error = quote["total_usd"], None
        except (ValueError, OSError) as failure:
            estimate, error = None, str(failure)
        return {"token": token, "audiences": list(AUDIENCE_PROFILES), "estimate": estimate,
                "configuration_error": error, "duration": [30, 60]}

    @server.get("/api/sources")
    def source_list():
        return [{"id": identity, "name": path.name, "video": f"/api/sources/{identity}/media",
                 "poster": f"/api/sources/{identity}/poster"} for identity, path in sources().items()]

    @server.get("/api/sources/{identity}/media")
    def source_media(identity: str):
        return FileResponse(source(identity))

    @server.get("/api/sources/{identity}/poster")
    def source_poster(identity: str):
        from pipeline.utils import ffmpeg_bin
        original = source(identity)
        stamp = data_hash([identity, original.stat().st_mtime_ns])[:20]
        thumbnail = studio.directory / "thumbnails" / f"{stamp}.jpg"
        if not thumbnail.exists():
            thumbnail.parent.mkdir(parents=True, exist_ok=True)
            temporary = thumbnail.with_name(f"{stamp}-{uuid.uuid4().hex}.jpg")
            try:
                subprocess.run([ffmpeg_bin(), "-nostdin", "-v", "error", "-ss", "1", "-i", str(original),
                                "-frames:v", "1", "-vf", "scale=640:-2", str(temporary)],
                               check=True, capture_output=True, timeout=30)
                temporary.replace(thumbnail)
            finally:
                temporary.unlink(missing_ok=True)
        return FileResponse(thumbnail)

    @server.post("/api/runs/from-source/{identity}")
    def use_source(identity: str):
        from pipeline.utils import media_duration
        path = source(identity)
        return studio.snapshot(studio.add(path, path.name, media_duration(path)))

    @server.post("/api/runs")
    async def upload(file: UploadFile):
        from pipeline.utils import media_duration, media_size
        limit = int(os.environ.get("STUDIO_MAX_UPLOAD_BYTES", 2 * 1024 ** 3))
        path = studio.directory / "uploads" / f"{uuid.uuid4().hex}.mp4"
        path.parent.mkdir(parents=True, exist_ok=True)
        total = 0
        try:
            with path.open("xb") as stream:
                while chunk := await file.read(1024 * 1024):
                    total += len(chunk)
                    if total > limit:
                        raise HTTPException(413, "Video exceeds the configured upload limit.")
                    stream.write(chunk)
            duration = await run_in_threadpool(media_duration, path)
            width, height = await run_in_threadpool(media_size, path)
            if duration <= 0 or width <= 0 or height <= 0:
                raise ValueError("Empty video")
            return studio.snapshot(studio.add(path, Path(file.filename or "lecture.mp4").name, duration))
        except HTTPException:
            path.unlink(missing_ok=True)
            raise
        except Exception as error:
            path.unlink(missing_ok=True)
            raise HTTPException(422, "This file could not be read as a video.") from error
        finally:
            await file.close()

    @server.get("/api/runs")
    def runs():
        with studio.lock:
            return [studio.snapshot(item) for item in reversed(list(studio.sessions.values()))]

    @server.get("/api/runs/{identity}")
    def run(identity: str):
        return studio.snapshot(record(identity))

    @server.get("/api/runs/{identity}/media/{kind}")
    def run_media(identity: str, kind: str):
        current = record(identity)
        state = current.get("state") or {}
        path = current["source"] if kind == "source" else state.get("storyboard_preview_path") if kind == "preview" else None
        if not path or not Path(path).resolve().is_relative_to(Path(root).resolve()) or not Path(path).is_file():
            raise HTTPException(404, "Media unavailable")
        return FileResponse(path)

    @server.post("/api/runs/{identity}/analyze")
    def analyze(identity: str, body: Analyze):
        def execute(current, progress):
            current["state"].update(storyboard_approval=None, storyboard_preview_revision=None)
            studio.update(identity, current["state"], export_id=None)
            state = workflow.analyze(current["source"], body.options.model_dump(), progress)
            return studio.snapshot(studio.update(identity, state, export_id=None))
        return submit(identity, "analyze", body, execute)

    @server.patch("/api/runs/{identity}/selection")
    def selection(identity: str, body: Selection):
        with studio.lock, jobs.lock:
            current = available(identity, body.revision)
            try:
                state = workflow.select(current["state"], [clip.model_dump() for clip in body.clips])
            except Exception as error:
                raise HTTPException(422, str(error)) from error
            return studio.snapshot(studio.update(identity, state, export_id=None))

    @server.post("/api/runs/{identity}/preview")
    def preview(identity: str, body: Action):
        def execute(current, progress):
            current["state"].update(storyboard_approval=None, storyboard_preview_revision=None)
            studio.update(identity, current["state"], export_id=None)
            state = workflow.generate(current["state"], progress,
                                      publish=lambda early: studio.update(identity, copy.deepcopy(early), export_id=None))
            return studio.snapshot(studio.update(identity, state, export_id=None))
        return submit(identity, "preview", body, execute)

    @server.post("/api/runs/{identity}/approval")
    def approval(identity: str, body: Approval):
        with studio.lock, jobs.lock:
            current = available(identity, body.revision)
            try:
                state = workflow.approve(current["state"], body.reviewer, body.attested)
            except Exception as error:
                raise HTTPException(422, str(error)) from error
            return studio.snapshot(studio.update(identity, state))

    @server.post("/api/runs/{identity}/exports")
    def export_run(identity: str, body: Action):
        def execute(current, progress):
            state, destination = workflow.export(current["state"], progress)
            export_id = catalog.identity(destination)
            studio.update(identity, state, export_id=export_id)
            progress(desc="Assessing exported video")
            from pipeline.assess import assess_export_automatically
            assess_export_automatically(destination)
            return {"export_id": export_id}
        return submit(identity, "export", body, execute)

    @server.get("/api/jobs")
    def job_list():
        with jobs.lock:
            return list(jobs.items.values())[-50:]

    @server.get("/api/jobs/{identity}")
    def job(identity: str):
        with jobs.lock:
            if identity not in jobs.items:
                raise HTTPException(404, "Job not found")
            return dict(jobs.items[identity])

    def folder(identity):
        value = catalog.find(identity)
        if value is None:
            raise HTTPException(404, "Export not found")
        return value

    @server.get("/api/exports")
    def exports():
        return {"exports": catalog.items()}

    @server.get("/api/exports/{identity}")
    def export(identity: str):
        return catalog.describe(folder(identity), detailed=True)

    @server.get("/api/exports/{identity}/files/{name}")
    def artifact(identity: str, name: str, download: bool = False):
        directory = folder(identity)
        if name not in ("compilation.mp4", "captions.srt", "post.txt") and not (name.startswith("compilation-") and name.endswith(".zip")):
            raise HTTPException(404, "Artifact not found")
        try:
            path = catalog.file(directory, name)
        except ValueError as error:
            raise HTTPException(404, "Artifact not found") from error
        return FileResponse(path, filename=name if download else None)

    @server.post("/api/exports/{identity}/reveal")
    def reveal(identity: str):
        subprocess.run(["open", "-R", str(catalog.file(folder(identity), "compilation.mp4"))], check=True, timeout=10)
        return {"revealed": True}

    @server.post("/api/exports/{identity}/assessment")
    def assess(identity: str, body: Action):
        directory = folder(identity)
        def execute(progress):
            from pipeline.assess import assess_export_automatically
            progress(desc="Checking video, audio and captions")
            assess_export_automatically(directory)
            return {"export_id": identity}
        try:
            return jobs.submit(f"export:{identity}", "assessment", body.key, execute, data_hash(body.model_dump()))
        except ValueError as error:
            raise HTTPException(409, str(error)) from error

    @server.get("/api/exports/{identity}/poster")
    def poster(identity: str):
        directory = folder(identity)
        manifest = json.loads(catalog.file(directory, "selection.json").read_text())
        name = (manifest.get("asset_inventory", {}).get("opening") or {}).get("file")
        try:
            if not name or Path(name).suffix.lower() not in (".png", ".jpg", ".jpeg"):
                raise ValueError("Poster unavailable")
            return FileResponse(catalog.file(directory, name))
        except ValueError as error:
            raise HTTPException(404, "Poster unavailable") from error

    distribution = Path(root) / "frontend" / "dist"
    if (distribution / "assets").is_dir():
        server.mount("/assets", StaticFiles(directory=distribution / "assets"), name="assets")

    @server.get("/")
    def frontend():
        if not (distribution / "index.html").is_file():
            raise HTTPException(503, "Build the frontend with npm run build first.")
        return FileResponse(distribution / "index.html")

    return server