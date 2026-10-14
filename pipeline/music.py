"""Bundled instrumental music library (assets/music): validated manifest and a deterministic per-audience pick."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from pipeline.project import file_hash

LIBRARY = Path(__file__).resolve().parent.parent / "assets" / "music"
AUDIENCES = ("Researcher", "General public", "Kids")
REQUIRED = ("file", "audience", "title", "license", "source", "credit")


def load_library(root: Path | None = None) -> list[dict]:
    """Return the manifest tracks with absolute paths; raise ValueError if anything is unusable."""
    root = Path(root or LIBRARY)
    try:
        manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
        entries = manifest["tracks"]
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise ValueError(f"Music library manifest is missing or unreadable at {root}/manifest.json ({exc}). "
                         "Run tools/generate_music.py or set music_source to generated.") from None
    tracks = []
    for entry in entries:
        if not isinstance(entry, dict) or any(not isinstance(entry.get(key), str) or not entry[key].strip() for key in REQUIRED):
            raise ValueError("Every music track needs file, audience, title, license, source and credit.")
        if entry.get("redistributable") is not True:
            raise ValueError(f"Music track {entry['title']} must explicitly permit redistribution in the export ZIP.")
        if entry["audience"] not in AUDIENCES:
            raise ValueError(f"Music track {entry['title']} names an unknown audience.")
        path = (root / entry["file"]).resolve()
        if not path.is_relative_to(root.resolve()) or not path.is_file():
            raise ValueError(f"Music track file is missing: {entry['file']}.")
        tracks.append({**entry, "path": str(path)})
    missing = [audience for audience in AUDIENCES if not any(track["audience"] == audience for track in tracks)]
    if missing:
        raise ValueError("Music library has no track for " + ", ".join(missing) + ".")
    return tracks


def choose_track(audience: str, key: str, root: Path | None = None) -> dict:
    """Pick one of the audience's tracks from a stable key, so the same selection always gets the same music."""
    candidates = [track for track in load_library(root) if track["audience"] == audience]
    if not candidates:
        raise ValueError(f"No bundled music for {audience}.")
    return candidates[int(hashlib.sha256(key.encode("utf-8")).hexdigest(), 16) % len(candidates)]


def library_asset(audience: str, key: str, root: Path | None = None) -> dict:
    """A media asset for the storyboard: hashed like any generated asset, with its licence and credit."""
    track = choose_track(audience, key, root)
    return {"path": track["path"], "sha256": file_hash(Path(track["path"])), "kind": "music", "provider": "library",
            "title": track["title"], "license": track["license"], "source": track["source"], "credit": track["credit"]}
