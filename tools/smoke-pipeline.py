"""Real local media / worker / TTS / PDF / export integration on synthetic QA media.

No ASR quality or automatic translation claim: the source transcript and target
translation below are manually supplied. No API requests or model downloads.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtCore import QEventLoop, QTimer
from PySide6.QtWidgets import QApplication
from mathbridge.application.coordinator import Coordinator
from mathbridge.application.artifacts import validate_render_plan
from mathbridge.media.engine import MediaEngine
from mathbridge.storage.project_store import ProjectStore


def perform(coordinator, action, *, expect_error=None):
    loop = QEventLoop()
    data = {}
    def finished(stage, result):
        data["result"] = result
    def failed(stage, message):
        data["error"] = message
    def busy(value):
        if not value:
            loop.quit()
    coordinator.task_finished.connect(finished)
    coordinator.task_failed.connect(failed)
    coordinator.busy_changed.connect(busy)
    timer = QTimer()
    timer.setSingleShot(True)
    timer.timeout.connect(loop.quit)
    timer.start(120000)
    try:
        action()
        loop.exec()
        if coordinator.busy:
            coordinator.cancel()
            raise RuntimeError("Worker timed out")
        if expect_error:
            assert expect_error in data.get("error", ""), data
        elif "error" in data:
            raise RuntimeError(data["error"])
        assert data, "Worker produced no completion"
        return data.get("result", {})
    finally:
        timer.stop()
        coordinator.task_finished.disconnect(finished)
        coordinator.task_failed.disconnect(failed)
        coordinator.busy_changed.disconnect(busy)


def main():
    app = QApplication([])
    folder = ROOT / "tmp" / ("pipeline-qa-" + uuid.uuid4().hex[:8])
    folder.mkdir(parents=True)
    engine = MediaEngine()
    source = folder / "人工测试 数学课程.mp4"
    subprocess.run([engine.ffmpeg_path, "-hide_banner", "-loglevel", "error", "-y",
                    "-f", "lavfi", "-i", "color=c=0x1d2f47:s=640x360:r=25:d=12",
                    "-f", "lavfi", "-i", "sine=frequency=440:duration=12:sample_rate=48000",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-af", "volume=0.05", str(source)], check=True)
    subtitle = folder / "source.srt"
    subtitle.write_text("1\n00:00:01,000 --> 00:00:08,000\nConsider x squared plus one.\n", encoding="utf-8")
    reports = []
    for source_lang, target_lang in (("en", "zh-CN"), ("zh-CN", "en")):
        coordinator = Coordinator()
        project_dir = folder / (source_lang + "-to-" + target_lang)
        perform(coordinator, lambda: coordinator.create_project(str(source), str(project_dir), source_lang, target_lang, "合成媒体工程验证"))
        perform(coordinator, lambda: coordinator.import_subtitles(str(subtitle)))
        if source_lang == "zh-CN":
            coordinator.update_segment("seg-1", {"source_text": "考虑 x 的平方加一。"})
        perform(coordinator, lambda: coordinator.run_stage("analyze"))
        for formula in list(coordinator.project["formulas"]):
            coordinator.update_formula(formula["id"], {"latex": "x^2+1", "spoken_target": "x 的平方加一" if target_lang == "zh-CN" else "x squared plus one", "review": "confirmed"})
        segment = coordinator.project["segments"][0]
        placeholders = " ".join("{{" + fid + "}}" for fid in segment["formula_ids"])
        plain = "考虑 x 的平方加一。" if target_lang == "zh-CN" else "Consider x squared plus one."
        coordinator.update_segment("seg-1", {"target_text": ("考虑 " if target_lang == "zh-CN" else "Consider ") + placeholders if placeholders else plain,
                                             "subtitle_plain": plain, "tts_text": plain, "review": "confirmed"})
        perform(coordinator, lambda: coordinator.run_stage("dub"))
        preview = perform(coordinator, lambda: coordinator.run_stage("preview"))
        assert any(Path(p).suffix == '.mp4' for p in preview['outputs'])
        # This offline media test supplies its teaching note as a fixture. Real
        # AI generation is covered separately by the opt-in qa-course-ai tool.
        previous = json.loads(json.dumps(coordinator.project))
        candidate = json.loads(json.dumps(previous))
        current_segment = coordinator.project["segments"][0]
        candidate["notes"] = [dict(id="note-qa", kind="course", heading="测试课程小节", body=current_segment["target_text"],
                                   segment_ids=["seg-1"], formula_ids=current_segment["formula_ids"], review="confirmed")]
        candidate["revision"] += 1
        coordinator._save_edit(candidate, previous)
        result = perform(coordinator, lambda: coordinator.run_stage("export", {"formats": ["srt", "vtt", "ass", "pdf", "video", "wav", "markdown", "json"], "background": "dub_only"}))
        plan_path = next(Path(p) for p in result["outputs"] if Path(p).name == "render-plan.json")
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
        validate_render_plan(plan, coordinator.project, project_dir)
        video = next(Path(p) for p in result["outputs"] if Path(p).suffix == ".mp4")
        probe = engine.probe(video)
        assert probe["video_streams"] and probe["audio_streams"]
        assert abs(probe["duration_ms"] - 12000) <= 300
        subprocess.run([engine.ffmpeg_path, '-hide_banner', '-loglevel', 'error', '-i', str(video), '-f', 'null', '-'],
                       capture_output=True, check=True)
        coordinator.update_segment("seg-1", {"tts_text": plain + (" 请注意。" if target_lang == "zh-CN" else " Please note.")})
        perform(coordinator, lambda: coordinator.run_stage("export", {"formats": ["video"], "background": "dub_only"}), expect_error="STALE_AUDIO")
        # Exporting original audio with translated subtitles is a separate explicit strategy.
        original = perform(coordinator, lambda: coordinator.run_stage("export", {"formats": ["video", "srt"], "background": "original"}))
        reports.append({"direction": source_lang + " -> " + target_lang, "outputs": result["outputs"], "duration_ms": probe["duration_ms"], "original_export": original["outputs"], "revision": plan["project_revision"]})
    report = {"scope": "Synthetic media with manually supplied transcript and translation; no ASR/API quality validation", "results": reports}
    (ROOT / "artifacts").mkdir(exist_ok=True)
    (ROOT / "artifacts/pipeline-report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print("PASS: real QProcess, both language directions, Windows TTS, PDF, SRT/VTT/ASS, video/WAV, hashes, stale audio rejection, original-audio export")
    print(f"QA outputs: {folder}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
