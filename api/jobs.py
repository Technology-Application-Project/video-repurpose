import json
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

from pipeline.project import atomic_json


class Jobs:
    def __init__(self, directory: Path):
        self.directory = directory
        self.lock = threading.RLock()
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="studio")
        self.items = {}
        for path in directory.glob("*.json"):
            try:
                item = json.loads(path.read_text())
                if item["status"] in ("queued", "running"):
                    item.update(status="interrupted", message="Server restarted. Review this run before retrying.")
                    atomic_json(path, item)
                self.items[item["id"]] = item
            except (OSError, ValueError, KeyError):
                continue

    def update(self, identity, **values):
        with self.lock:
            self.items[identity].update(values)
            atomic_json(self.directory / f"{identity}.json", self.items[identity])

    def submit(self, run, action, key, execute, signature=""):
        with self.lock:
            for item in self.items.values():
                if item["run"] == run and item["key"] == key:
                    if item["action"] != action or item.get("signature", "") != signature:
                        raise ValueError("Idempotency key belongs to a different action.")
                    return dict(item)
            if any(item["run"] == run and item["status"] in ("queued", "running") for item in self.items.values()):
                raise ValueError("This run already has an active job.")
            identity = uuid.uuid4().hex
            item = {"id": identity, "run": run, "action": action, "key": key, "signature": signature, "status": "queued", "stage": "Queued",
                    "created": datetime.now(timezone.utc).isoformat(), "message": None}
            self.items[identity] = item
            self.update(identity)

        def progress(value=None, desc=None, **unused):
            if desc:
                self.update(identity, stage=desc)

        def work():
            self.update(identity, status="running", stage=action.capitalize())
            try:
                result = execute(progress)
                self.update(identity, status="succeeded", stage="Complete", result=result)
            except Exception as error:
                message = str(error) if isinstance(error, (ValueError, RuntimeError)) or type(error).__name__ == "Error" else f"{type(error).__name__}: operation failed. Check local configuration."
                self.update(identity, status="failed", stage="Needs attention", message=message[:600])

        self.executor.submit(work)
        return dict(item)