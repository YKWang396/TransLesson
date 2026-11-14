"""Run a real course through the application coordinator in an isolated project."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtCore import QEventLoop, QTimer
from PySide6.QtWidgets import QApplication
from mathbridge.application.coordinator import Coordinator
from mathbridge.application.reference import load_reference_words, suggest_reference, suggest_auxiliary_references
from mathbridge.application.artifacts import validate_render_plan
from mathbridge.domain.project import file_hash, validate_project
from mathbridge.media.engine import MediaEngine
from mathbridge.storage.credentials import load_text_secret
from mathbridge.storage.project_store import ProjectStore, atomic_json
from mathbridge.ui.settings import load_settings


def perform(coordinator, action, timeout):
    loop = QEventLoop()
    outcome = {}
    def finished(stage, result): outcome["result"] = result
    def failed(stage, message): outcome["error"] = message
    def busy(value):
        if not value: loop.quit()
    coordinator.task_finished.connect(finished)
    coordinator.task_failed.connect(failed)
    coordinator.busy_changed.connect(busy)
    timer = QTimer(); timer.setSingleShot(True); timer.timeout.connect(loop.quit)
    timer.start(timeout * 1000)
    try:
        action()
        if coordinator.busy: loop.exec()
        if coordinator.busy:
            coordinator.cancel()
            raise RuntimeError("Stage timed out; resume the isolated project to continue")
        if "error" in outcome: raise RuntimeError(outcome["error"])
        if "result" not in outcome: raise RuntimeError("Stage produced no result")
        return outcome["result"]
    finally:
        timer.stop()
        coordinator.task_finished.disconnect(finished)
        coordinator.task_failed.disconnect(failed)
        coordinator.busy_changed.disconnect(busy)


def main():
    sys.stdout.reconfigure(encoding="utf8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--resume", type=Path)
    parser.add_argument("--seconds", type=int, default=0, help="0 processes the entire video")
    parser.add_argument("--start", type=int, default=0)
    parser.add_argument("--budget", type=float, default=5)
    parser.add_argument("--source-language", choices=["en","zh-CN"], default="en")
    parser.add_argument("--target-language", choices=["en","zh-CN"], default="zh-CN")
    parser.add_argument("--timeout", type=int, default=3600, help="Per-stage timeout in seconds")
    parser.add_argument("--real", action="store_true", required=True, help="Use the configured paid text API")
    parser.add_argument("--reference", action="store_true", help="Use authorized source voice with the local service")
    args = parser.parse_args()
    if args.seconds < 0 or args.start < 0 or args.budget <= 0: parser.error("Invalid limits")
    settings = load_settings()
    if not settings.get("remember_api_key") or not settings.get("allow_text_api"):
        parser.error("Save the API Key and enable course-text processing in the application first")
    settings["api_key"] = load_text_secret(settings)
    if not settings["api_key"]: parser.error("Saved API Key is unavailable")
    settings["budget_cny"] = min(settings["budget_cny"], args.budget)
    root = args.resume.resolve() if args.resume else ROOT/"tmp"/("verify-"+uuid.uuid4().hex[:10])
    root.mkdir(parents=True, exist_ok=True)
    source = args.source.resolve()
    source_sha = file_hash(source)
    report_path = root/"verification.json"
    report = json.loads(report_path.read_text("utf8")) if report_path.exists() else dict(
        source_sha256=source_sha, reference=args.reference, seconds=args.seconds, start=args.start, stages={})
    if any(report.get(k) != v for k,v in dict(source_sha256=source_sha, reference=args.reference,
                                             seconds=args.seconds, start=args.start).items()):
        parser.error("Resume parameters do not match this verification project")
    atomic_json(report_path, report)
    print("PROJECT", root, flush=True)
    app = QApplication([])
    engine = MediaEngine()
    coordinator = Coordinator(); coordinator.settings.update(settings)
    coordinator.progress.connect(lambda stage,i,n,message: print(stage,i,n,message,flush=True))
    if (root/"project.json").exists():
        coordinator.open_project(root)
        if coordinator.project["languages"] != dict(source=args.source_language,target=args.target_language):
            parser.error("Resume languages do not match this project")
    else:
        if args.seconds:
            clip = root/"verification-source.mp4"
            subprocess.run([engine.ffmpeg_path,"-hide_banner","-loglevel","error","-nostdin","-y",
                "-ss",str(args.start),"-i",str(source),"-t",str(args.seconds),"-vf","scale=640:-2",
                "-c:v","libx264","-preset","ultrafast","-c:a","aac",str(clip)],check=True,timeout=300)
            source = clip
        perform(coordinator, lambda:coordinator.create_project(str(source),str(root),args.source_language,args.target_language,"课程运行验证"),args.timeout)
    try:
        for stage in ("transcribe","sentences","analyze","translate","notes","reference","dub","export"):
            if stage in report["stages"]: continue
            started = time.monotonic()
            print("START",stage,flush=True)
            if stage == "reference":
                if args.reference:
                    project = coordinator.project; sid = project["speakers"][0]["id"]
                    words = load_reference_words(project,root)
                    primary = suggest_reference(project,sid,words)
                    if not primary: raise RuntimeError("No complete 3–10 second reference utterance")
                    profile = dict(provider="gpt_sovits",reference_start_ms=primary["start_ms"],
                        reference_end_ms=primary["end_ms"],reference_text=primary["reference_text"],
                        reference_language=primary["reference_language"],speed=1.0,
                        auxiliary_references=suggest_auxiliary_references(project,sid,words,primary))
                    coordinator.update_speaker(sid,dict(voice_mode="reference",voice_authorized=True,voice_profile=profile))
                result = {}
            else:
                options = dict(alignment_mode="adaptive",alignment_max_speed=1.5,alignment_borrow_ms=1000)
                if stage == "export":
                    # Exercise explicit source preservation, without certifying model math.
                    for formula in list(coordinator.project["formulas"]):
                        if formula["review"] == "needs_review": coordinator.update_formula(formula["id"],dict(review="preserve_source"))
                    options.update(formats=["video","wav","srt","vtt","ass","pdf","markdown","json"],background="dub_only")
                result = perform(coordinator,lambda:coordinator.run_stage(stage,options),args.timeout)
            validate_project(coordinator.project)
            text=" ".join(s["source_text"] for s in coordinator.project["segments"])
            normalized=" ".join(text.split()) if args.source_language=="en" else "".join(text.split())
            digest=hashlib.sha256(normalized.encode()).hexdigest()
            if stage=="transcribe":
                report.update(source_text_sha256=digest,source_units=len(text.split()))
            elif "source_text_sha256" in report:
                assert digest==report["source_text_sha256"],"Course source text changed during processing"
            report["stages"][stage] = dict(seconds=round(time.monotonic()-started,2),
                segments=len(coordinator.project["segments"]),metadata=result.get("metadata",{}),outputs=result.get("outputs",[]))
            atomic_json(report_path,report)
            print("PASS",stage,flush=True)
        export = report["stages"]["export"]["outputs"]
        plan = json.loads(next(Path(p) for p in export if Path(p).name=="render-plan.json").read_text("utf8"))
        validate_render_plan(plan,coordinator.project,root)
        video = next(Path(p) for p in export if Path(p).suffix==".mp4")
        info = engine.probe(video)
        assert info["video_streams"] and info["audio_streams"]
        assert abs(info["duration_ms"]-coordinator.project["source"]["duration_ms"]) < 500
        subprocess.run([engine.ffmpeg_path,"-v","error","-i",str(video),"-f","null","-"],check=True,capture_output=True,timeout=args.timeout)
        assert file_hash(args.source.resolve()) == source_sha
        report.update(status="passed",usage=coordinator.usage,video_duration_ms=info["duration_ms"],
                      output_hashes={Path(p).name:file_hash(Path(p)) for p in export})
        report.pop("error",None)
        atomic_json(report_path,report)
        print("PASS complete course workflow",root,flush=True)
    except BaseException as error:
        report.update(status="failed",error=str(error).replace(settings["api_key"],"[redacted]"),usage=coordinator.usage)
        atomic_json(report_path,report)
        raise


if __name__ == "__main__": main()
