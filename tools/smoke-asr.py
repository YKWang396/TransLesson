"""Bounded, real local ASR through the source or frozen worker. No downloads."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import threading
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from mathbridge.media.engine import MediaEngine


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--seconds", type=int, default=30)
    parser.add_argument("--frozen", action="store_true")
    parser.add_argument("--trace", action="store_true", help="Dump source-worker thread stacks while diagnosing a stall")
    parser.add_argument("--timeout", type=int, default=180)
    args = parser.parse_args()
    if not 5 <= args.seconds <= 120:
        parser.error("--seconds must be between 5 and 120")
    source = Path(args.source).resolve()
    model = Path(args.model).resolve()
    if not source.is_file() or not all((model / name).is_file() for name in ("model.bin", "config.json", "tokenizer.json")):
        parser.error("Source or complete local model not found")
    folder = ROOT / "tmp" / ("real-asr-" + uuid.uuid4().hex[:10])
    folder.mkdir(parents=True)
    clip = folder / "course-excerpt.mp4"
    media = MediaEngine()
    print("Preparing local excerpt:", folder, flush=True)
    subprocess.run([media.ffmpeg_path, "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
        "-f", "lavfi", "-i", f"color=c=0x1d2f47:s=640x360:r=25:d={args.seconds}",
        "-i", str(source), "-t", str(args.seconds), "-map", "0:v:0", "-map", "1:a:0",
        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac", str(clip)],
        stdin=subprocess.DEVNULL, capture_output=True, check=True, timeout=90)
    environment = os.environ.copy()
    environment.update(PYTHONIOENCODING="utf-8", PYTHONUTF8="1", QT_QPA_PLATFORM="offscreen")
    if args.frozen:
        system32 = Path(os.environ.get("WINDIR", "C:/Windows")) / "System32"
        environment["PATH"] = os.pathsep.join((str(system32), str(system32 / "WindowsPowerShell/v1.0")))
        environment.pop("PYTHONPATH", None)
        executable = [str(ROOT / "dist/TransLesson/translesson-worker.exe"), "--worker"]
    else:
        environment["PYTHONPATH"] = str(ROOT / "src")
        if args.trace:
            executable = [sys.executable, "-u", "-c", "import faulthandler,runpy; faulthandler.dump_traceback_later(15, repeat=True); runpy.run_module('mathbridge.worker', run_name='__main__')"]
        else:
            executable = [sys.executable, "-u", "-m", "mathbridge.worker"]
    project = None
    reports = []

    def run(stage, options):
        command = dict(stage=stage, request_id="req-"+uuid.uuid4().hex, project_dir=str(folder),
            source_path=str(clip), project=project, project_revision=project["revision"] if project else 0,
            settings=dict(whisper_model_path=str(model)), options=options, artifacts=[])
        started = time.monotonic()
        records = []
        events = queue.Queue()
        with (folder / f"{stage}.stderr.log").open("w", encoding="utf-8") as errors:
            process = subprocess.Popen(executable, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=errors, text=True, encoding="utf-8", env=environment, cwd=folder,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
            def read():
                for line in process.stdout:
                    events.put(line)
                events.put(None)
            reader = threading.Thread(target=read, daemon=True)
            reader.start()
            process.stdin.write(json.dumps(command, ensure_ascii=False)+"\n")
            process.stdin.flush()
            try:
                while True:
                    if time.monotonic()-started > args.timeout:
                        raise TimeoutError(f"{stage} exceeded {args.timeout}s; see {folder}")
                    try:
                        line = events.get(timeout=.2)
                    except queue.Empty:
                        continue
                    if line is None:
                        break
                    record = json.loads(line)
                    records.append(record)
                    if record.get("type") == "progress":
                        message = record.get("message") or record.get("stage")
                        print(f"{time.monotonic()-started:.1f}s {stage}: {message}", flush=True)
                process.wait(timeout=5)
            finally:
                try:
                    if process.poll() is None:
                        process.stdin.write('{"type":"cancel"}\n')
                        process.stdin.flush()
                    process.stdin.close()
                except (OSError, ValueError):
                    pass
                if process.poll() is None:
                    if os.name == "nt":
                        subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], capture_output=True)
                        if process.poll() is None:
                            process.kill()
                    else:
                        process.kill()
                    process.wait(timeout=10)
                process.stdout.close()
                reader.join(timeout=2)
        (folder / f"{stage}.jsonl").write_text("\n".join(json.dumps(record, ensure_ascii=False) for record in records), encoding="utf-8")
        done = next((record for record in records if record.get("type") == "done"), {})
        elapsed = round(time.monotonic()-started, 2)
        reports.append(dict(stage=stage, elapsed_seconds=elapsed, status=done.get("status"), code=done.get("code")))
        print(stage, done.get("status", f"exit {process.returncode}"), elapsed, flush=True)
        if done.get("status") != "succeeded":
            raise RuntimeError(done.get("message") or f"Worker exited {process.returncode}; see {folder}")
        return done["result"]

    try:
        project = run("create", dict(source_language="en", target_language="zh-CN", title="真实原课片段 ASR 验证"))["project"]
        project = run("transcribe", {})["project"]
        assert project["segments"]
        report = dict(frozen=args.frozen, excerpt_seconds=args.seconds, qa_folder=str(folder),
                      segments=len(project["segments"]), stages=reports,
                      scope="Real course excerpt and local Whisper model; engineering and timing check, no full-course accuracy claim")
        (ROOT / "artifacts").mkdir(exist_ok=True)
        destination = ROOT / "artifacts" / ("asr-frozen-report.json" if args.frozen else "asr-report.json")
        destination.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print("PASS:", report["segments"], "recognized segments;", destination, flush=True)
    except Exception:
        (folder / "failure-report.json").write_text(json.dumps(dict(frozen=args.frozen, stages=reports), ensure_ascii=False, indent=2), encoding="utf-8")
        raise


if __name__ == "__main__":
    main()
