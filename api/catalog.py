import hashlib
import json
from pathlib import Path


class Catalog:
    def __init__(self, root: Path):
        self.root = root.resolve()

    def folders(self):
        roots = [self.root / "exports", *self.root.glob("work/*/test-exports"),
                 *self.root.glob("work/*/*/test-exports")]
        folders = []
        for base in roots:
            for manifest in base.glob("*/*/selection.json"):
                folder = manifest.parent
                if folder.resolve() != folder.absolute() or not (folder / "compilation.mp4").is_file():
                    continue
                folders.append(folder)
        return sorted(set(folders), key=lambda folder: folder.stat().st_mtime, reverse=True)

    def identity(self, folder):
        return hashlib.sha256(str(folder.relative_to(self.root)).encode()).hexdigest()[:20]

    def find(self, identity):
        return next((folder for folder in self.folders() if self.identity(folder) == identity), None)

    def file(self, folder, name):
        path = (folder / name).resolve()
        if not path.is_relative_to(folder.resolve()) or not path.is_file():
            raise ValueError("Artifact unavailable")
        return path

    def describe(self, folder, detailed=False):
        manifest = json.loads(self.file(folder, "selection.json").read_text())
        identity = self.identity(folder)
        timeline = manifest.get("timeline") or []
        draft = manifest.get("storyboard") or {}
        result = {"id": identity, "title": manifest.get("intro_title") or folder.name,
                  "audience": (manifest.get("selection_context") or {}).get("target_audience") or "Unknown",
                  "aspect": manifest.get("aspect_ratio", "16:9"), "created": folder.stat().st_mtime,
                  "duration": round(sum(item.get("duration", 0) for item in timeline), 2),
                  "clip_count": len(manifest.get("clips", [])),
                  "test_export": "test-exports" in folder.parts,
                  "approved": (manifest.get("storyboard_approval") or {}).get("kind") == "human",
                  "video": f"/api/exports/{identity}/files/compilation.mp4",
                  "poster": f"/api/exports/{identity}/poster" if (manifest.get("asset_inventory") or {}).get("opening") else None}
        if detailed:
            files = [path.name for path in folder.iterdir() if path.name in ("compilation.mp4", "captions.srt", "post.txt")
                     or path.name.startswith("compilation-") and path.suffix == ".zip"]
            assessment = None
            for path in sorted(folder.glob("assessments/*/assessment.json"), key=lambda item: item.stat().st_mtime, reverse=True):
                if not path.resolve().is_relative_to(folder.resolve()):
                    continue
                saved = json.loads(path.read_text())
                if saved.get("assessment_type") == "objective":
                    from pipeline.assess import metric_story
                    assessment = {key: saved.get(key) for key in ("metric_version", "technical_status", "counts", "metrics", "publication_status")}
                    assessment["metrics"] = [{**metric, **metric_story(metric)} for metric in assessment["metrics"] or []]
                    break
            result.update(folder=str(folder), files=files, timeline=timeline,
                          clips=[{key: clip.get(key) for key in ("id", "title", "takeaway", "start", "end", "excerpt")}
                                 for clip in manifest.get("clips", [])],
                          storyboard={key: draft.get(key) for key in ("opening", "ending", "transitions", "evidence")},
                          post=self.file(folder, "post.txt").read_text() if (folder / "post.txt").exists() else "",
                          assessment=assessment, structure=self.structure(folder, manifest))
        return result

    def structure(self, folder, manifest):
        from pipeline.structure import structure_check
        from pipeline.utils import media_duration
        try:
            seconds = media_duration(self.file(folder, "compilation.mp4"))
        except Exception:
            seconds = None
        return structure_check(manifest, seconds)

    def items(self):
        items = []
        for folder in self.folders():
            try:
                items.append(self.describe(folder))
            except (ValueError, OSError, TypeError):
                continue
        return items