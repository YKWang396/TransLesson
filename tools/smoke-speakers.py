"""Exercise per-speaker reference voices through real worker subprocesses.

Run with the project's Python, optionally with --frozen to test the portable
worker with developer runtimes removed from its PATH. The local HTTP server is
a GPT-SoVITS protocol stub returning synthetic tones, not a voice clone model.
No model downloads, paid API requests, ASR or diarization quality claims.
"""
from __future__ import annotations

import argparse
from array import array
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import threading
import traceback
import uuid
import wave


ROOT = Path(__file__).resolve().parents[1]


def file_hash(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def tone_wav(frequency, duration=.4, rate=32000):
    samples = array("h", (round(6000 * math.sin(2 * math.pi * frequency * index / rate))
                          for index in range(round(rate * duration))))
    if sys.byteorder != "little":
        samples.byteswap()
    output = io.BytesIO()
    with wave.open(output, "wb") as audio:
        audio.setparams((1, 2, rate, 0, "NONE", "not compressed"))
        audio.writeframes(samples.tobytes())
    return output.getvalue()


@contextmanager
def protocol_stub(project_dir):
    """Record requests and require references to be real, local project WAVs."""
    requests, failures = [], []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_GET(self):
            if self.path == "/openapi.json":
                data = {"paths": {"/tts": {"post": {"requestBody": {"required": True}}}}}
                self.reply(200, "application/json", json.dumps(data).encode("utf-8"))
            else:
                self.reply(404, "application/json", b"{}")

        def do_POST(self):
            try:
                assert self.path == "/tts", self.path
                size = int(self.headers.get("Content-Length", "0"))
                assert 0 < size <= 256 * 1024
                payload = json.loads(self.rfile.read(size))
                reference = Path(payload["ref_audio_path"]).resolve()
                assert reference.is_relative_to(project_dir / "audio/references")
                assert payload["text_lang"] == "zh"
                assert payload["prompt_lang"] == "en"
                assert payload["streaming_mode"] is False
                assert payload["media_type"] == "wav"
                with wave.open(str(reference), "rb") as audio:
                    assert (audio.getnchannels(), audio.getsampwidth(), audio.getframerate()) == (1, 2, 32000)
                    duration = audio.getnframes() / audio.getframerate()
                    assert duration == 4.0
                    samples = audio.readframes(audio.getnframes())
                    assert len(samples) == audio.getnframes() * 2
                item = {"path": self.path, "payload": payload, "reference_sha256": file_hash(reference),
                        "reference_duration_seconds": duration}
                requests.append(item)
                frequency = 280 if payload["prompt_text"] == "The derivative is positive." else 420
                self.reply(200, "audio/wav", tone_wav(frequency))
            except Exception as exc:
                failures.append(str(exc) or type(exc).__name__)
                self.reply(400, "application/json", json.dumps({"message": failures[-1]}).encode("utf-8"))

        def reply(self, status, content_type, body):
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=lambda: server.serve_forever(poll_interval=.05), daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", requests, failures
    finally:
        server.shutdown()
        server.server_close()
        thread.join(2)


def worker_environment(frozen):
    environment = os.environ.copy()
    environment.update(PYTHONIOENCODING="utf-8", PYTHONUTF8="1", QT_QPA_PLATFORM="offscreen")
    if frozen:
        system32 = Path(environment.get("WINDIR", "C:/Windows")) / "System32"
        environment["PATH"] = os.pathsep.join((str(system32), str(system32 / "WindowsPowerShell/v1.0")))
        for key in ("PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV", "MATHBRIDGE_NODE", "IMAGEIO_FFMPEG_EXE", "FFMPEG_BINARY"):
            environment.pop(key, None)
    else:
        environment["PYTHONPATH"] = str(ROOT / "src")
    return environment


def find_ffmpeg(frozen, bundle):
    if frozen:
        binaries = sorted((bundle / "_internal/imageio_ffmpeg/binaries").glob("ffmpeg*.exe"))
        if len(binaries) != 1:
            raise RuntimeError(f"Expected one bundled FFmpeg executable, found {len(binaries)} in {bundle}")
        return binaries[0]
    import imageio_ffmpeg
    return Path(imageio_ffmpeg.get_ffmpeg_exe())


def execute_worker(stage, *, project, project_dir, source, settings, options, frozen, bundle,
                   environment, log_dir, results):
    request_id = "req-speakers-" + uuid.uuid4().hex
    command = dict(type="command", request_id=request_id, stage=stage, project_dir=str(project_dir),
                   project=project, project_revision=project["revision"] if project else 0,
                   source_path=str(source), settings=settings, options=options, artifacts=[])
    executable = [str(bundle / "translesson-worker.exe"), "--worker"] if frozen else [sys.executable, "-m", "mathbridge.worker"]
    process = subprocess.run(executable, input=json.dumps(command, ensure_ascii=False) + "\n",
                             text=True, encoding="utf-8", capture_output=True, env=environment,
                             cwd=bundle if frozen else ROOT, timeout=60)
    stdout = log_dir / (request_id + ".stdout.jsonl")
    stderr = log_dir / (request_id + ".stderr.txt")
    stdout.write_text(process.stdout, encoding="utf-8")
    stderr.write_text(process.stderr, encoding="utf-8")
    messages = [json.loads(line) for line in process.stdout.splitlines() if line.strip()]
    done = [message for message in messages if message.get("type") == "done"]
    if process.returncode or len(done) != 1 or done[0].get("status") != "succeeded":
        raise RuntimeError(f"Worker {stage} failed: {done!r}; see {stderr}")
    result = done[0]["result"]
    results.append(dict(stage=stage, options=options, outputs=result.get("outputs", []),
                        stdout=str(stdout), stderr=str(stderr), returncode=process.returncode))
    return result


def check_output(result):
    assert len(result["artifacts"]) == len(result["outputs"]) == 1, result
    path = Path(result["outputs"][0])
    artifact = result["artifacts"][0]
    assert path.is_file() and artifact["kind"] == "tts"
    assert artifact["sha256"] == file_hash(path)
    with wave.open(str(path), "rb") as audio:
        assert audio.getnframes() / audio.getframerate() == .4
        assert audio.getframerate() == 32000
    return path, artifact


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--frozen", action="store_true", help="Use the portable worker and its bundled FFmpeg, with isolated PATH")
    parser.add_argument("--bundle", type=Path, default=ROOT / "dist/TransLesson")
    args = parser.parse_args()
    bundle = args.bundle.resolve()
    report_path = ROOT / "artifacts" / ("speakers-frozen-report.json" if args.frozen else "speakers-report.json")
    folder = ROOT / "tmp" / ("speakers-frozen-qa-" if args.frozen else "speakers-qa-")
    folder = folder.with_name(folder.name + uuid.uuid4().hex[:8])
    project_dir = folder / "多人声音工程"
    log_dir = folder / "worker-logs"
    project_dir.mkdir(parents=True)
    log_dir.mkdir()
    report_path.parent.mkdir(exist_ok=True)
    results, checks = [], []
    report = dict(status="running", execution="frozen" if args.frozen else "source",
                  scope="Synthetic media and a loopback GPT-SoVITS protocol stub; no actual voice-cloning model, ASR, diarization or voice-similarity validation.",
                  voice_model_tested=False, worker_path_isolated=args.frozen,
                  qa_directory=str(folder), results=results, checks=checks,
                  created_at=datetime.now(timezone.utc).isoformat())
    try:
        environment = worker_environment(args.frozen)
        if args.frozen and not (bundle / "translesson-worker.exe").is_file():
            raise RuntimeError(f"Portable worker is missing: {bundle / 'translesson-worker.exe'}")
        ffmpeg = find_ffmpeg(args.frozen, bundle)
        report["ffmpeg"] = dict(path=str(ffmpeg), sha256=file_hash(ffmpeg), bundled=args.frozen)
        source = folder / "人工合成 两人原声.mp4"
        # Distinct tones in the first/second six seconds make reference hashes
        # different, without pretending this source contains genuine speakers.
        subprocess.run([str(ffmpeg), "-hide_banner", "-loglevel", "error", "-y",
                        "-f", "lavfi", "-i", "color=c=0x1d2f47:s=320x180:r=10:d=12",
                        "-f", "lavfi", "-i", "sine=frequency=220:sample_rate=16000:duration=6",
                        "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=16000:duration=6",
                        "-filter_complex", "[1:a][2:a]concat=n=2:v=0:a=1[a]", "-map", "0:v:0", "-map", "[a]",
                        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(source)],
                       env=environment, capture_output=True, timeout=30, check=True)
        checks.append("Real FFmpeg generated a 12-second video with distinct synthetic reference tones")
        common = dict(project_dir=project_dir, source=source, frozen=args.frozen, bundle=bundle,
                      environment=environment, log_dir=log_dir, results=results)
        created = execute_worker("create", project=None, settings={},
                                  options=dict(source_language="en", target_language="zh-CN", title="多人原声协议验证"), **common)
        project = created["project"]
        assert abs(project["source"]["duration_ms"] - 12000) <= 300
        project["speakers"] = []
        for identifier, start, label, text, translation in (
            ("teacher", 500, "老师", "The derivative is positive.", "导数为正。"),
            ("student", 6500, "学生", "Could you explain that again?", "可以再解释一下吗？"),
        ):
            project["speakers"].append(dict(id=identifier, label=label, voice_mode="reference", voice_authorized=True,
                reference_evidence=dict(id="ref-" + identifier, kind="speech", start_ms=start, end_ms=start + 4000),
                voice_profile=dict(provider="gpt_sovits", reference_start_ms=start, reference_end_ms=start + 4000,
                                   reference_text=text, reference_language="en", speed=1.0)))
            project["segments"].append(dict(id="seg-" + identifier, speaker_id=identifier, start_ms=start, end_ms=start + 4000,
                source_text=text, target_text=translation, subtitle_plain=translation, tts_text=translation,
                formula_ids=[], term_ids=[], review="needs_review"))
        (project_dir / "project.json").write_text(json.dumps(project, ensure_ascii=False, indent=2), encoding="utf-8")
        (project_dir / "inputs").mkdir()
        (project_dir / "inputs/source.link.json").write_text(json.dumps(created["metadata"]["source_link"], ensure_ascii=False, indent=2), encoding="utf-8")
        with protocol_stub(project_dir) as (endpoint, requests, failures):
            settings = dict(voice_clone_api_url=endpoint, voice_clone_model="protocol-stub-v1")

            def sample(identifier):
                return execute_worker("dub", project=project, settings=settings,
                                      options=dict(segment_ids=["seg-" + identifier], sample_only=True), **common)

            teacher = sample("teacher")
            student = sample("student")
            teacher_path, teacher_artifact = check_output(teacher)
            student_path, student_artifact = check_output(student)
            assert len(requests) == 2 and not failures
            assert requests[0]["payload"]["prompt_text"] == "The derivative is positive."
            assert requests[1]["payload"]["prompt_text"] == "Could you explain that again?"
            assert requests[0]["payload"]["ref_audio_path"] != requests[1]["payload"]["ref_audio_path"]
            assert requests[0]["reference_sha256"] != requests[1]["reference_sha256"]
            assert teacher_artifact["input_fingerprint"] != student_artifact["input_fingerprint"]
            assert teacher_path != student_path and file_hash(teacher_path) != file_hash(student_path)
            checks.append("Each speaker used their own real four-second reference WAV and corresponding source transcript")
            checks.append("Both real worker processes sent valid cross-language POST /tts requests and saved checked 32kHz PCM")
            cached = sample("teacher")
            check_output(cached)
            assert len(requests) == 2 and cached["outputs"] == teacher["outputs"]
            checks.append("Unchanged speaker inputs reused the existing TTS cache without another HTTP request")
            settings["voice_clone_model"] = "protocol-stub-v2"
            changed = sample("teacher")
            changed_path, changed_artifact = check_output(changed)
            assert len(requests) == 3 and changed_path != teacher_path
            assert changed_artifact["input_fingerprint"] != teacher_artifact["input_fingerprint"]
            assert requests[2]["payload"]["ref_audio_path"] == requests[0]["payload"]["ref_audio_path"]
            checks.append("Changing the clone model version regenerated TTS while retaining the same original reference")
            report["protocol_stub"] = dict(endpoint=endpoint, requests=requests, failures=failures,
                                             description="Synthetic PCM responses; no GPT-SoVITS model was loaded")
        report["status"] = "passed"
        print("PASS: real video, worker subprocesses, per-speaker references, local GPT protocol, cache reuse and model-version invalidation")
        print("Scope: protocol stub only; actual voice-cloning quality was not tested")
        print(f"Report: {report_path}")
        return 0
    except Exception as exc:
        report.update(status="failed", error=str(exc), traceback=traceback.format_exc())
        print(f"FAIL: {exc}", file=sys.stderr)
        print(f"Report: {report_path}", file=sys.stderr)
        return 1
    finally:
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
