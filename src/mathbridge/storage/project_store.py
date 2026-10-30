from __future__ import annotations

import copy
import json
import os
import uuid
from pathlib import Path

from mathbridge.domain.project import safe_path, validate_project


def atomic_json(path: Path, value) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


class ProjectStore:
    def __init__(self, root):
        self.root = Path(root).resolve()

    def load(self) -> dict:
        project = json.loads((self.root / "project.json").read_text(encoding="utf-8"))
        validate_project(project)
        # Resolve every stored internal path before trusting it.
        safe_path(self.root, project["source"]["path"])
        for formula in project["formulas"]:
            for ev in formula["evidence"]:
                if ev.get("frame_path"):
                    safe_path(self.root, ev["frame_path"])
        runtime = self.read("runtime.json", {})
        for stage in runtime.get("stages", {}).values():
            if stage.get("status") == "running":
                stage["status"] = "interrupted"
        self.write("runtime.json", runtime)
        return project

    def save(self, project: dict) -> None:
        validate_project(project)
        history = safe_path(self.root, f"history/revision-{project['revision']:06d}.json")
        if history.exists():
            old = json.loads(history.read_text(encoding="utf-8"))
            if old != project:
                raise ValueError("REVISION_CONFLICT: 不允许改写历史快照")
        else:
            atomic_json(history, project)
        atomic_json(self.root / "project.json", project)

    def read(self, relative: str, default=None):
        path = safe_path(self.root, relative)
        if not path.exists():
            return copy.deepcopy(default)
        return json.loads(path.read_text(encoding="utf-8"))

    def write(self, relative: str, value) -> None:
        atomic_json(safe_path(self.root, relative), value)

    @property
    def source_path(self) -> str:
        link = self.read("inputs/source.link.json", {})
        original = link.get("absolute_path", "")
        if original and Path(original).is_file():
            return original
        # A copied project can carry its media beside project.json. Accept only
        # a matching source hash, never a same-name but different lecture.
        if original:
            candidate = self.root / Path(original).name
            if candidate.is_file() and candidate.stat().st_size == link.get("size"):
                from mathbridge.domain.project import file_hash
                if file_hash(candidate) == link.get("sha256"):
                    self.write("inputs/source.link.json", {**link, "absolute_path": str(candidate),
                               "mtime_ns": candidate.stat().st_mtime_ns})
                    return str(candidate)
        return original
