"""Use two installed Windows voices on separate turns of a synthetic video."""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import threading
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from mathbridge.domain.project import file_hash, new_project, validate_project
from mathbridge.media.engine import MediaEngine
from mathbridge.providers.tts.windows import list_voices
from mathbridge.storage.project_store import ProjectStore
from mathbridge.worker.__main__ import execute


def main():
    voices = [voice for voice in list_voices() if voice["language"].startswith("en")]
    if len(voices) < 2:
        raise RuntimeError("This smoke requires two installed English SAPI voices")
    folder = ROOT / "tmp" / ("system-speakers-" + uuid.uuid4().hex[:8])
    folder.mkdir(parents=True)
    engine = MediaEngine()
    source = folder / "synthetic.mp4"
    subprocess.run([engine.ffmpeg_path, "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi",
                    "-i", "color=c=0x1d2f47:s=640x360:r=25:d=12", "-f", "lavfi",
                    "-i", "sine=frequency=400:duration=12:sample_rate=48000",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(source)], check=True)
    project = new_project("两种系统声音工程验证", dict(path="inputs/source.link.json", sha256=file_hash(source),
                           duration_ms=12000, timeline_origin_ms=0), "zh-CN", "en")
    project["speakers"] = [dict(id=identifier, label=voice["name"], voice_mode="standard",
                                voice_profile=dict(provider="windows", voice_name=voice["id"]))
                           for identifier, voice in zip(("teacher", "student"), voices[:2])]
    for index, speaker in enumerate(project["speakers"]):
        project["segments"].append(dict(id=f"seg-{index+1}", speaker_id=speaker["id"],
            start_ms=index*6000, end_ms=index*6000+5000, source_text="导数是正数。",
            target_text="The derivative is positive.", subtitle_plain="The derivative is positive.",
            tts_text="The derivative is positive.", formula_ids=[], term_ids=[], review="confirmed"))
    validate_project(project)
    store = ProjectStore(folder)
    store.save(project)
    store.write("inputs/source.link.json", dict(absolute_path=str(source), sha256=project["source"]["sha256"]))
    base = dict(project_dir=str(folder), project=project, project_revision=1, source_path=str(source),
                settings={}, artifacts=[])
    dubbed = execute({**base, "request_id": "req-"+uuid.uuid4().hex, "stage": "dub", "options": {}},
                     threading.Event(), lambda _: None)
    clips = [artifact for artifact in dubbed["artifacts"] if artifact["kind"] == "tts"]
    assert len(clips) == 2 and clips[0]["sha256"] != clips[1]["sha256"], clips
    exported = execute({**base, "request_id": "req-"+uuid.uuid4().hex, "stage": "export",
                        "artifacts": dubbed["artifacts"], "options": dict(formats=["video", "wav", "srt"], background="dub_only")},
                       threading.Event(), lambda _: None)
    video = next(Path(path) for path in exported["outputs"] if path.endswith(".mp4"))
    info = engine.probe(video)
    assert info["audio_streams"] and info["video_streams"] and abs(info["duration_ms"] - 12000) < 300
    subprocess.run([engine.ffmpeg_path, "-hide_banner", "-loglevel", "error", "-i", str(video), "-f", "null", "-"],
                   capture_output=True, check=True)
    report = dict(scope="Two actual installed SAPI voices, identical target text on separate synthetic turns; no diarization or voice-clone quality claim",
                  speakers=project["speakers"], artifacts=clips, outputs=exported["outputs"])
    (ROOT / "artifacts").mkdir(exist_ok=True)
    (ROOT / "artifacts/system-speakers-report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print("PASS: two distinct installed Windows voices, aligned mix, subtitles and complete MP4 decode")
    print("QA folder:", folder)


if __name__ == "__main__":
    main()
