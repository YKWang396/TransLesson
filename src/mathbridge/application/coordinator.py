from __future__ import annotations

import copy
import json
import os
import shutil
import sys
import time
import uuid
from pathlib import Path

from PySide6.QtCore import QObject, QProcess, QProcessEnvironment, QTimer, Signal

from mathbridge.domain.project import changed_copy, export_issues, safe_path, validate_project
from mathbridge.storage.project_store import ProjectStore, atomic_json


class Coordinator(QObject):
    project_changed = Signal(dict)
    progress = Signal(str, int, int, str)
    task_finished = Signal(str, dict)
    task_failed = Signal(str, str)
    busy_changed = Signal(bool)
    log_message = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.project = None
        self.project_dir = None
        self.busy = False
        self.settings = dict(text_provider="deepseek", model="deepseek-flash", api_base="https://api.deepseek.com",
                             allow_text_api=False, allow_image_api=False, budget_cny=5.0, max_calls=100,
                             input_price_per_million=2.0, output_price_per_million=8.0, voice="", speech_rate=0)
        self._process = None
        self._buffer = b""
        self._command = None
        self._done = False
        self._cancelled = False
        self._pipeline = []
        self._undo = []
        self._stderr = b""
        self._reaper = None
        self._editor_dirty = False
        self._usage = {}
        self._progress_saved_at = 0.0

    @property
    def usage(self):
        return copy.deepcopy(self._usage)

    def set_editor_dirty(self, dirty):
        self._editor_dirty = bool(dirty)

    @property
    def source_path(self):
        return ProjectStore(self.project_dir).source_path if self.project_dir else ""

    def open_project(self, directory):
        if self.busy:
            raise ValueError("请先停止当前任务")
        path = Path(directory).resolve()
        if path.is_file():
            path = path.parent
        project = ProjectStore(path).load()
        self.project_dir = path
        self.project = project
        self._usage = ProjectStore(path).read("runtime.json", {}).get("usage", {})
        self._recover_reservations(ProjectStore(path))
        self._undo.clear()
        self.project_changed.emit(copy.deepcopy(project))
        self.log_message.emit(f"已打开 {project['title']} · revision {project['revision']}")
        if not Path(self.source_path).is_file():
            self.log_message.emit("原视频未找到；校对仍可继续，请重新定位视频以进行媒体操作")

    def create_project(self, source, directory, source_language="en", target_language="zh-CN", title=""):
        if self.busy:
            raise ValueError("正在处理其他任务")
        root = Path(directory).resolve()
        if (root / "project.json").exists():
            raise ValueError("项目已存在，请使用打开项目")
        root.mkdir(parents=True, exist_ok=True)
        self._start("create", dict(title=title, source_language=source_language, target_language=target_language),
                    project_dir=root, source_path=str(Path(source).resolve()), project=None)

    def relink_source(self, path):
        self._start("relink", {}, source_path=str(Path(path).resolve()))

    def import_subtitles(self, path):
        self.run_stage("import_subtitles", {"path": str(Path(path).resolve())})

    def _edit(self, collection, entity_id, changes):
        if self.project is None:
            raise ValueError("请先打开项目")
        previous = copy.deepcopy(self.project)
        original = next((item for item in self.project[collection] if item["id"] == entity_id), None)
        if original is not None and all(key in original and original[key] == value for key, value in changes.items()):
            return
        candidate = changed_copy(self.project, collection, entity_id, changes, _validate=False)
        self._save_edit(candidate, previous)

    def _save_edit(self, candidate, previous):
        store = ProjectStore(self.project_dir)
        store.save(candidate)
        self._undo.append(previous)
        self._undo = self._undo[-30:]
        self.project = candidate
        runtime = store.read("runtime.json", {"stages": {}})
        runtime["edited_revision"] = candidate["revision"]
        # Retain immutable files; cache validity is evaluated from exact content fingerprints.
        for name in ("dub", "preview", "notes", "export"):
            if runtime.setdefault("stages", {}).get(name, {}).get("status") == "succeeded":
                runtime["stages"][name]["status"] = "stale"
        store.write("runtime.json", runtime)
        self.project_changed.emit(copy.deepcopy(candidate))

    def update_segment(self, segment_id, changes, *, _derived_fresh=False):
        previous = copy.deepcopy(self.project)
        original = next(s for s in self.project["segments"] if s["id"] == segment_id)
        changes = dict(changes)
        source_changed = "source_text" in changes and changes["source_text"] != original["source_text"]
        target_changed = "target_text" in changes and changes["target_text"] != original["target_text"]
        if source_changed or target_changed:
            derived = ("target_text", "subtitle_plain", "tts_text") if source_changed else ("subtitle_plain", "tts_text")
            for field in (() if _derived_fresh else derived):
                # Preserve deliberately supplied new derived text; reject reuse of old text.
                if changes.get(field, original[field]) == original[field]:
                    changes[field] = ""
            changes["review"] = "needs_review"
        if "speaker_id" in changes and changes["speaker_id"] != original["speaker_id"]:
            changes["review"] = "needs_review"
        if all(key in original and original[key] == value for key, value in changes.items()):
            return
        candidate = changed_copy(self.project, "segments", segment_id, changes, _validate=False)
        if source_changed:
            for formula in candidate["formulas"]:
                if formula["id"] in original["formula_ids"]:
                    formula["review"] = "needs_review"
        if source_changed or target_changed:
            for note in candidate["notes"]:
                if segment_id in note["segment_ids"]:
                    note["review"] = "needs_review"
        self._save_edit(candidate, previous)

    def update_simple_subtitle(self, segment_id, subtitle_text, *, changes=None):
        if not self.project:
            raise ValueError("请先打开项目")
        original = next((segment for segment in self.project["segments"] if segment["id"] == segment_id), None)
        if original is None:
            raise ValueError("片段不存在")
        edits = dict(changes or {})
        for key in ("target_text", "subtitle_plain", "tts_text"):
            edits.pop(key, None)
        if edits.get("source_text", original["source_text"]) != original["source_text"]:
            # Source correction invalidates the previous translation. The view
            # reloads the cleared fields instead of retaining an old hidden TTS.
            self.update_segment(segment_id, edits)
            return
        fresh = subtitle_text != original["subtitle_plain"] or bool(subtitle_text.strip()) and any(not original[key].strip() for key in ("target_text", "tts_text"))
        if fresh:
            from mathbridge.application.review import simple_subtitle_changes
            edits.update(simple_subtitle_changes(self.project, original, subtitle_text))
        if any(original.get(key) != value for key, value in edits.items()):
            self.update_segment(segment_id, edits, _derived_fresh=fresh)

    def bulk_confirm_segments(self, segment_ids=None):
        """Confirm explicitly selected complete subtitles in one undoable save."""
        if not self.project:
            raise ValueError("请先打开项目")
        if self.busy:
            raise ValueError("请先等待当前任务完成")
        selected = set(segment_ids) if segment_ids is not None else {s["id"] for s in self.project["segments"]}
        if not selected.issubset({s["id"] for s in self.project["segments"]}):
            raise ValueError("片段不存在")
        candidate = copy.deepcopy(self.project)
        confirmed = skipped = 0
        for segment in candidate["segments"]:
            if segment["id"] not in selected or segment["review"] == "confirmed":
                continue
            if any(not segment[key].strip() for key in ("target_text", "subtitle_plain", "tts_text")):
                skipped += 1
                continue
            segment["review"] = "confirmed"
            confirmed += 1
        if confirmed:
            candidate["revision"] += 1
            self._save_edit(candidate, copy.deepcopy(self.project))
        return dict(confirmed=confirmed, skipped=skipped)

    def preserve_pending_formulas(self):
        """Explicitly retain original evidence without confirming math guesses."""
        from mathbridge.application.issues import preservable_formula_ids
        if not self.project:
            raise ValueError("请先打开项目")
        if self.busy:
            raise ValueError("请先等待当前任务完成")
        identifiers = set(preservable_formula_ids(self.project, self.project_dir))
        candidate = copy.deepcopy(self.project)
        for formula in candidate["formulas"]:
            if formula["id"] in identifiers:
                formula["review"] = "preserve_source"
        if identifiers:
            candidate["revision"] += 1
            self._save_edit(candidate, copy.deepcopy(self.project))
        return dict(preserved=len(identifiers), remaining=sum(f["review"] == "needs_review" for f in candidate["formulas"]))

    def repair_obsolete_formulas(self):
        """Remove only provable former substring-rule errors, preserving source."""
        from mathbridge.application.issues import obsolete_rule_formula_ids
        if not self.project:
            raise ValueError("请先打开项目")
        if self.busy:
            raise ValueError("请先等待当前任务完成")
        identifiers = set(obsolete_rule_formula_ids(self.project))
        candidate = copy.deepcopy(self.project)
        affected = []
        for segment in candidate["segments"]:
            if identifiers.intersection(segment["formula_ids"]):
                segment["formula_ids"] = [identifier for identifier in segment["formula_ids"] if identifier not in identifiers]
                segment.update(target_text="", subtitle_plain="", tts_text="", review="needs_review")
                affected.append(segment["id"])
        affected_set = set(affected)
        original_formulas = {formula["id"]: formula for formula in candidate["formulas"]}
        for note in candidate["notes"]:
            removed = identifiers.intersection(note["formula_ids"])
            if removed or affected_set.intersection(note["segment_ids"]):
                note["formula_ids"] = [identifier for identifier in note["formula_ids"] if identifier not in identifiers]
                for identifier in removed:
                    note["body"] = note["body"].replace("{{" + identifier + "}}", original_formulas[identifier]["raw"])
                note["review"] = "needs_review"
                # A formula-only course note needs its original source segment
                # after its obsolete formula reference has been removed.
                if note["kind"] == "course" and not note["formula_ids"] and not note["segment_ids"]:
                    note["segment_ids"] = list(affected)
        candidate["formulas"] = [formula for formula in candidate["formulas"] if formula["id"] not in identifiers]
        if identifiers:
            candidate["revision"] += 1
            self._save_edit(candidate, copy.deepcopy(self.project))
        return dict(removed=len(identifiers), affected=len(affected), segment_ids=affected)

    def merge_segments(self, first_id, second_id):
        self.bulk_merge_segments([[first_id, second_id]])
        return first_id

    def bulk_merge_segments(self, groups):
        """Merge adjacent same-speaker groups in one revision, keeping evidence."""
        if not self.project:
            raise ValueError("请先打开项目")
        if self.busy:
            raise ValueError("请先等待当前任务完成")
        ordered = sorted(self.project["segments"], key=lambda item: item["start_ms"])
        positions = {segment["id"]: index for index, segment in enumerate(ordered)}
        consumed = set()
        checked = []
        for group in groups:
            if len(group) < 2 or any(identifier not in positions for identifier in group):
                raise ValueError("每组合并至少需要两个存在的相邻片段")
            indices = [positions[identifier] for identifier in group]
            if indices != list(range(indices[0], indices[0] + len(indices))) or consumed.intersection(group):
                raise ValueError("只能按时间顺序合并不重复的相邻片段")
            originals = [ordered[index] for index in indices]
            if len({segment["speaker_id"] for segment in originals}) != 1:
                raise ValueError("说话人不同，请先核对说话人后再合并")
            checked.append(originals)
            consumed.update(group)
        if not checked:
            return dict(merged=0, groups=0, segment_ids=[])
        candidate = copy.deepcopy(self.project)
        by_id = {segment["id"]: segment for segment in candidate["segments"]}
        replacements = {}
        kept_ids = []
        for originals in checked:
            first = by_id[originals[0]["id"]]
            kept_ids.append(first["id"])
            first["end_ms"] = originals[-1]["end_ms"]
            for field in ("source_text", "target_text", "subtitle_plain", "tts_text"):
                language = candidate["languages"]["source" if field == "source_text" else "target"]
                separator = "" if language.startswith("zh") else " "
                texts = [segment[field].strip() for segment in originals]
                first[field] = separator.join(texts) if all(texts) else ""
            for field in ("formula_ids", "term_ids"):
                first[field] = list(dict.fromkeys(identifier for segment in originals for identifier in segment[field]))
            first["review"] = "needs_review"
            for original in originals[1:]:
                replacements[original["id"]] = first["id"]
        candidate["segments"] = [segment for segment in candidate["segments"] if segment["id"] not in replacements]
        for note in candidate["notes"]:
            if consumed.intersection(note["segment_ids"]):
                note["segment_ids"] = list(dict.fromkeys(replacements.get(identifier, identifier) for identifier in note["segment_ids"]))
                note["review"] = "needs_review"
        candidate["revision"] += 1
        self._save_edit(candidate, copy.deepcopy(self.project))
        return dict(merged=len(replacements), groups=len(checked), segment_ids=kept_ids)

    def add_speaker(self, label):
        if not self.project:
            raise ValueError("请先打开项目")
        label = str(label).strip()
        if not label:
            raise ValueError("请填写说话人名称")
        previous = copy.deepcopy(self.project)
        candidate = copy.deepcopy(self.project)
        identifier = "speaker-" + uuid.uuid4().hex[:12]
        candidate["speakers"].append(dict(id=identifier, label=label, voice_mode="standard"))
        candidate["revision"] += 1
        validate_project(candidate)
        self._save_edit(candidate, previous)
        return identifier

    def split_segment(self, segment_id, split_ms, source_offset):
        """Split at an explicit source-text boundary; derived text needs review."""
        if not self.project:
            raise ValueError("请先打开项目")
        original = next((s for s in self.project["segments"] if s["id"] == segment_id), None)
        if original is None:
            raise ValueError("片段不存在")
        if (not isinstance(split_ms, int) or isinstance(split_ms, bool)
                or not original["start_ms"] < split_ms < original["end_ms"]):
            raise ValueError("拆分时间须位于片段起止时间之间")
        if (not isinstance(source_offset, int) or isinstance(source_offset, bool)
                or not 0 < source_offset < len(original["source_text"])):
            raise ValueError("请将原文光标放在两位说话人的文字分界处")
        first_text = original["source_text"][:source_offset].strip()
        second_text = original["source_text"][source_offset:].strip()
        if not first_text or not second_text:
            raise ValueError("拆分后的两段原文都不能为空")
        previous = copy.deepcopy(self.project)
        candidate = copy.deepcopy(self.project)
        index = next(i for i, segment in enumerate(candidate["segments"]) if segment["id"] == segment_id)
        first, second = candidate["segments"][index], copy.deepcopy(original)
        second_id = "seg-" + uuid.uuid4().hex[:16]
        first.update(source_text=first_text, end_ms=split_ms, target_text="", subtitle_plain="", tts_text="", review="needs_review")
        second.update(id=second_id, source_text=second_text, start_ms=split_ms, target_text="", subtitle_plain="", tts_text="", review="needs_review")
        candidate["segments"].insert(index + 1, second)
        for formula in candidate["formulas"]:
            if formula["id"] in original["formula_ids"]:
                formula["review"] = "needs_review"
        for note in candidate["notes"]:
            if segment_id in note["segment_ids"]:
                location = note["segment_ids"].index(segment_id) + 1
                note["segment_ids"].insert(location, second_id)
                note["review"] = "needs_review"
        candidate["revision"] += 1
        validate_project(candidate)
        self._save_edit(candidate, previous)
        return second_id

    def update_speaker(self, speaker_id, changes):
        if not self.project:
            raise ValueError("请先打开项目")
        changes = copy.deepcopy(changes)
        speaker = next((s for s in self.project["speakers"] if s["id"] == speaker_id), None)
        if speaker is None:
            raise ValueError("说话人不存在")
        if "label" in changes:
            changes["label"] = changes["label"].strip()
        if "voice_profile" in changes:
            changes["voice_profile"] = {**speaker.get("voice_profile", {}), **changes["voice_profile"]}
        mode = changes.get("voice_mode", speaker["voice_mode"])
        if mode == "reference":
            profile = changes.get("voice_profile", speaker.get("voice_profile", {}))
            changes["reference_evidence"] = dict(id="ref-" + speaker_id[:60], kind="speech",
                start_ms=profile.get("reference_start_ms", 0), end_ms=profile.get("reference_end_ms", 0))
        self._edit("speakers", speaker_id, changes)

    def update_formula(self, entity_id, changes):
        previous = copy.deepcopy(self.project)
        original = next(f for f in self.project["formulas"] if f["id"] == entity_id)
        if all(key in original and original[key] == value for key, value in changes.items()):
            return
        candidate = changed_copy(self.project, "formulas", entity_id, changes, _validate=False)
        latex_changed = "latex" in changes and changes["latex"] != original["latex"]
        reading_changed = "spoken_target" in changes and changes["spoken_target"] != original["spoken_target"]
        if latex_changed or reading_changed:
            for segment in candidate["segments"]:
                if entity_id in segment["formula_ids"]:
                    segment["review"] = "needs_review"
                    if latex_changed:
                        segment["subtitle_plain"] = ""
                    if reading_changed:
                        segment["tts_text"] = ""
            for note in candidate["notes"]:
                if entity_id in note["formula_ids"]:
                    note["review"] = "needs_review"
        validate_project(candidate)
        self._save_edit(candidate, previous)

    def update_term(self, entity_id, changes):
        previous = copy.deepcopy(self.project)
        original = next(t for t in self.project["glossary"] if t["id"] == entity_id)
        if all(key in original and original[key] == value for key, value in changes.items()):
            return
        candidate = changed_copy(self.project, "glossary", entity_id, changes, _validate=False)
        if any(k in changes and changes[k] != original[k] for k in ("source", "target", "context")):
            affected = set()
            for segment in candidate["segments"]:
                if entity_id in segment["term_ids"]:
                    segment.update(target_text="", subtitle_plain="", tts_text="", review="needs_review")
                    affected.add(segment["id"])
            for note in candidate["notes"]:
                if affected.intersection(note["segment_ids"]):
                    note["review"] = "needs_review"
        validate_project(candidate)
        self._save_edit(candidate, previous)

    def update_note(self, entity_id, changes):
        self._edit("notes", entity_id, changes)

    def add_visual_formula(self, segment_id, frame_path, bbox_xyxy=None, latex=None, time_ms=None):
        from mathbridge.providers.visual_math.pix2text import manual_candidate
        if not self.project:
            raise ValueError("请先建立项目")
        segment = next((s for s in self.project["segments"] if s["id"] == segment_id), None)
        if segment is None:
            raise ValueError("请先选中一个语音片段")
        image = Path(frame_path).resolve()
        if not image.is_relative_to(self.project_dir):
            raise ValueError("证据图必须位于项目目录")
        region = dict(frame_path=image.relative_to(self.project_dir).as_posix(), start_ms=int(time_ms if time_ms is not None else segment["start_ms"]), bbox_xyxy=bbox_xyxy or [0, 0, 1, 1])
        candidate = manual_candidate(self.project_dir, region, latex)
        previous = copy.deepcopy(self.project)
        project = copy.deepcopy(self.project)
        if any(f["id"] == candidate["id"] for f in project["formulas"]):
            raise ValueError("该片段已添加相同帧和区域的公式证据")
        project["formulas"].append(candidate)
        target = next(s for s in project["segments"] if s["id"] == segment_id)
        target["formula_ids"].append(candidate["id"])
        target["target_text"] = (target["target_text"] + " {{" + candidate["id"] + "}}").strip()
        target["review"] = "needs_review"
        project["revision"] += 1
        validate_project(project)
        self._save_edit(project, previous)

    def undo(self):
        if not self._undo:
            return
        candidate = copy.deepcopy(self._undo.pop())
        candidate["revision"] = self.project["revision"] + 1
        validate_project(candidate)
        ProjectStore(self.project_dir).save(candidate)
        self.project = candidate
        self.project_changed.emit(copy.deepcopy(candidate))

    def export_issues(self, formats=None):
        if self.project is None:
            return [dict(code="NO_PROJECT", message="请先打开项目", entity_id="", severity="error")]
        return export_issues(self.project, formats)

    def run_stage(self, stage, options=None):
        if stage == "translate_missing":
            self._pipeline = []
            self._start("translate", {**(options or {}), "missing_only": True})
            return
        if stage == "prepare_translation":
            if self.busy:
                raise ValueError("已有任务运行")
            if not self.project or not self.project["segments"]:
                raise ValueError("请先生成原文或导入字幕")
            self._pipeline = ["translate"]
            self._start("analyze", options or {})
            return
        if stage == "pipeline":
            if self.busy:
                raise ValueError("已有任务运行")
            self._pipeline = (["diarize"] if self.settings.get("diarization_enabled") else []) + ["analyze", "translate", "dub", "notes"]
            self._start("transcribe" if not self.project or not self.project["segments"] else self._pipeline.pop(0), options or {})
            return
        self._pipeline = []
        self._start(stage, options or {})

    def _start(self, stage, options, project_dir=None, source_path=None, project="current"):
        if self.busy:
            raise ValueError("同一时间仅可运行一个处理任务")
        root = Path(project_dir or self.project_dir or "").resolve()
        snapshot = copy.deepcopy(self.project) if project == "current" else project
        if stage != "create" and snapshot is None:
            raise ValueError("请先导入视频并创建项目")
        store = ProjectStore(root)
        settings = dict(self.settings)
        usage = store.read("runtime.json", {}).get("usage", {})
        request_id = "req-" + uuid.uuid4().hex
        settings.update(_project_dir=str(root), _request_id=request_id, usage_spent_cny=usage.get("reserved_total_cny", usage.get("cost_cny", 0.0)), usage_calls=usage.get("calls", 0))
        self._command = dict(type="command", stage=stage, request_id=request_id, project_dir=str(root),
                             source_path=source_path if source_path is not None else self.source_path,
                             project=snapshot, project_revision=snapshot["revision"] if snapshot else 0,
                             settings=settings, options=copy.deepcopy(options), artifacts=store.read("artifacts.json", []))
        self._buffer, self._stderr = b"", b""
        self._done, self._cancelled = False, False
        self._progress_saved_at = 0.0
        process = QProcess(self)
        environment = QProcessEnvironment.systemEnvironment()
        environment.insert("PYTHONPATH", str(Path(__file__).resolve().parents[2]))
        environment.insert("PYTHONIOENCODING", "utf-8")
        environment.insert("PYTHONUTF8", "1")
        process.setProcessEnvironment(environment)
        process.setWorkingDirectory(str(root))
        process.setProgram(sys.executable)
        if getattr(sys, "frozen", False):
            worker_executable = Path(sys.executable).with_name("mathbridge-worker.exe")
            if worker_executable.is_file():
                process.setProgram(str(worker_executable))
            process.setArguments(["--worker"])
        else:
            process.setArguments(["-m", "mathbridge.worker"])
        process.readyReadStandardOutput.connect(self._read_output)
        process.readyReadStandardError.connect(self._read_error)
        process.started.connect(self._send_command)
        process.errorOccurred.connect(self._process_error)
        process.finished.connect(self._process_finished)
        self._process = process
        self.busy = True
        self.busy_changed.emit(True)
        self._set_runtime("running")
        self.progress.emit(stage, 0, 0, "任务正在启动")
        process.start()

    def _send_command(self):
        self._process.write((json.dumps(self._command, ensure_ascii=False) + "\n").encode("utf-8"))

    def _read_error(self):
        # Never expose provider bodies or keys through logs.
        self._stderr = (self._stderr + bytes(self._process.readAllStandardError()))[-12000:]

    def _read_output(self):
        self._buffer += bytes(self._process.readAllStandardOutput())
        if len(self._buffer) > 32 * 1024 * 1024:
            self.cancel()
            self.task_failed.emit(self._command["stage"], "WORKER_PROTOCOL: 消息超过大小限制")
            return
        while b"\n" in self._buffer:
            line, self._buffer = self._buffer.split(b"\n", 1)
            try:
                message = json.loads(line.decode("utf-8"))
                if message.get("request_id") != self._command["request_id"]:
                    continue
                if message["type"] == "progress":
                    now = time.monotonic()
                    if not message.get("total") or now - self._progress_saved_at >= 2 or message.get("completed") == message.get("total"):
                        try:
                            store = ProjectStore(self._command["project_dir"])
                            runtime = store.read("runtime.json", {"stages": {}})
                            state = runtime.setdefault("stages", {}).setdefault(self._command["stage"], {})
                            state["last_progress"] = {key: message.get(key) for key in ("stage", "completed", "total", "message")}
                            state["updated_at"] = time.time()
                            store.write("runtime.json", runtime)
                            self._progress_saved_at = now
                        except OSError:
                            self.log_message.emit("进度记录暂时无法写入，当前任务仍在运行")
                    self.progress.emit(message.get("stage", ""), message.get("completed", 0), message.get("total", 0), message.get("message", ""))
                elif message["type"] == "done" and not self._done:
                    self._done = True
                    try:
                        self._receive_result(message)
                    except Exception as exc:
                        self._pipeline.clear()
                        self._set_runtime("failed", {"error": str(exc)[:2000]})
                        self.task_failed.emit(self._command["stage"], str(exc))
            except (ValueError, KeyError, TypeError) as exc:
                self.log_message.emit(f"WORKER_PROTOCOL: {type(exc).__name__}")

    def _set_runtime(self, status, extra=None):
        if not self._command:
            return
        store = ProjectStore(self._command["project_dir"])
        runtime = store.read("runtime.json", {"stages": {}})
        stages = runtime.setdefault("stages", {})
        previous = stages.get(self._command["stage"], {})
        state = dict(previous) if previous.get("request_id") == self._command["request_id"] else {"started_at": time.time()}
        state.update(status=status, request_id=self._command["request_id"], input_revision=self._command["project_revision"], updated_at=time.time(), **(extra or {}))
        stages[self._command["stage"]] = state
        store.write("runtime.json", runtime)

    def _receive_result(self, message):
        stage = self._command["stage"]
        status = message["status"]
        if status != "succeeded":
            self._pipeline.clear()
            self._record_usage(ProjectStore(self._command["project_dir"]), message.get("usage", {}))
            self._set_runtime(status, {"error": message.get("message", "")})
            self.task_failed.emit(stage, message.get("message", status))
            return
        result = message["result"]
        snapshot = self._command["project"]
        content_stages = {"transcribe", "import_subtitles", "diarize", "analyze", "translate", "notes"}
        # Edits in any text field already incremented revision, even without review confirmation.
        stale = stage in content_stages and (self._editor_dirty or not self.project or self.project["revision"] != self._command["project_revision"])
        store = ProjectStore(self._command["project_dir"])
        for absolute in result.get("outputs", []):
            path = Path(absolute).resolve()
            if not path.is_relative_to(store.root):
                raise ValueError("INVALID_PATH: worker 返回越界产物")
        self._record_usage(store, result.get("metadata", {}).get("usage", {}))
        if stale:
            recovery = "cache/rejected/" + self._command["request_id"] + ".json"
            store.write(recovery, result)
            self._set_runtime("stale", {"recovery_path": recovery})
            self._pipeline.clear()
            self.log_message.emit("处理期间内容已编辑；旧结果已保存为独立草稿，没有覆盖当前版本")
            self.task_finished.emit(stage, {"outputs": [], "metadata": {"stale": True}})
            return
        if stage == "create":
            self.project_dir = store.root
            self.project = result["project"]
            self._usage = store.read("runtime.json", {}).get("usage", {})
            store.save(self.project)
            store.write("inputs/source.link.json", result["metadata"]["source_link"])
            self._undo.clear()
            self.project_changed.emit(copy.deepcopy(self.project))
        elif stage == "relink":
            store.write("inputs/source.link.json", result["metadata"]["source_link"])
        elif stage in content_stages and "project" in result:
            candidate = result["project"]
            candidate["revision"] = self.project["revision"] + 1
            validate_project(candidate)
            self._undo.append(copy.deepcopy(self.project))
            store.save(candidate)
            self.project = candidate
            self.project_changed.emit(copy.deepcopy(candidate))
        registry = store.read("artifacts.json", [])
        for artifact in result.get("artifacts", []):
            safe_path(store.root, artifact["path"])
            registry = [a for a in registry if a["id"] != artifact["id"]]
            registry.append(artifact)
        store.write("artifacts.json", registry)
        runtime = store.read("runtime.json", {"stages": {}})
        metadata = result.get("metadata", {})
        runtime.setdefault("metadata", {})[stage] = metadata
        store.write("runtime.json", runtime)
        self._usage = runtime.get("usage", {})
        self._set_runtime("succeeded")
        if stage == "export" and self._command["options"].get("output_dir"):
            self._copy_exports(result)
        self.task_finished.emit(stage, result)

    def _record_usage(self, store, usage, request_id=None):
        request_id = request_id or self._command["request_id"]
        ledger = store.read(f"cache/api-reservations/{request_id}.json", {})
        if ledger:
            if ledger.get("request_id") != request_id:
                raise ValueError("INVALID_LEDGER: 请求账目 ID 不匹配")
            usage = dict(ledger.get("usage", {}))
            pending = ledger.get("pending_cost_cny", 0)
            usage["uncertain_cost_cny"] = usage.get("uncertain_cost_cny", 0) + pending
            usage["uncertain_requests"] = usage.get("uncertain_requests", 0) + int(pending > 0)
        if not usage:
            return
        runtime = store.read("runtime.json", {"stages": {}})
        normalized = {}
        self._merge_usage(normalized, usage)
        normalized = normalized["usage"]
        prior = runtime.setdefault("usage_requests", {}).get(request_id, {})
        delta = {key: value - prior.get(key, 0) for key, value in normalized.items() if key != "reserved_total_cny"}
        self._merge_usage(runtime, delta)
        runtime["usage_requests"][request_id] = normalized
        store.write("runtime.json", runtime)
        self._usage = runtime.get("usage", {})

    def _recover_reservations(self, store):
        directory = safe_path(store.root, "cache/api-reservations")
        if directory.is_dir():
            for path in directory.glob("req-*.json"):
                self._record_usage(store, {}, path.stem)

    @staticmethod
    def _merge_usage(runtime, usage):
        old = runtime.setdefault("usage", {})
        aliases = {"input_tokens": "prompt_tokens", "output_tokens": "completion_tokens", "cached_input_tokens": "cache_hit_tokens"}
        for key in ("cost_cny", "calls", "input_tokens", "output_tokens", "cached_input_tokens", "uncertain_cost_cny", "uncertain_requests"):
            old[key] = old.get(key, 0) + usage.get(key, usage.get(aliases.get(key, key), 0))
        old["reserved_total_cny"] = old.get("cost_cny", 0) + old.get("uncertain_cost_cny", 0)

    def _copy_exports(self, result):
        # All workers write inside the project; only the coordinator publishes to the user-selected destination.
        target_root = Path(self._command["options"]["output_dir"]).resolve()
        target_root.mkdir(parents=True, exist_ok=True)
        # Unique revision folder avoids overwriting existing exports or source files.
        target = target_root / Path(result["metadata"]["export_directory"]).name
        if target.exists():
            raise ValueError("OUTPUT_EXISTS: 目标导出目录已存在，请选择其他目录")
        temporary = target.with_name(target.name + ".partial-" + uuid.uuid4().hex[:8])
        temporary.mkdir()
        try:
            for absolute in result["outputs"]:
                shutil.copy2(absolute, temporary / Path(absolute).name)
            os.replace(temporary, target)
            result["outputs"] = [str(target / Path(p).name) for p in result["outputs"]]
            result["metadata"]["published_directory"] = str(target)
        except Exception:
            # Keep recoverable source exports; no success is reported for partial copies.
            raise

    def cancel(self):
        self._pipeline.clear()
        if not self.busy or self._process is None:
            return
        self._cancelled = True
        self._process.write(b'{"type":"cancel"}\n')
        self.log_message.emit("正在取消；等待当前安全检查点，超时后终止整个任务进程树")
        request_id = self._command["request_id"]
        QTimer.singleShot(5000, lambda: self._force_stop(request_id))

    def _force_stop(self, request_id):
        if not self.busy or not self._process or self._command["request_id"] != request_id:
            return
        if self._process.state() == QProcess.ProcessState.NotRunning:
            return
        if os.name == "nt":
            self._reaper = QProcess(self)
            self._reaper.start("taskkill", ["/PID", str(self._process.processId()), "/T", "/F"])
        else:
            self._process.kill()

    def _process_error(self, error):
        if error == QProcess.ProcessError.FailedToStart:
            self._done = True
            self._set_runtime("failed")
            self.busy = False
            self.busy_changed.emit(False)
            self.task_failed.emit(self._command["stage"], "WORKER_START_FAILED: " + self._process.errorString())

    def _process_finished(self, exit_code, exit_status):
        self._read_output()
        if not self._done:
            self._record_usage(ProjectStore(self._command["project_dir"]), {})
            status = "cancelled" if self._cancelled else "interrupted"
            self._set_runtime(status)
            self._pipeline.clear()
            self.task_failed.emit(self._command["stage"], "任务已取消，完成的缓存可复用" if self._cancelled else f"WORKER_CRASHED: 工作进程退出 ({exit_code})，项目校对已保留")
        self.busy = False
        self.busy_changed.emit(False)
        self._process.deleteLater()
        self._process = None
        self._stderr = b""
        if self._pipeline and not self._cancelled:
            next_stage = self._pipeline.pop(0)
            QTimer.singleShot(0, lambda: self._start(next_stage, {}))
