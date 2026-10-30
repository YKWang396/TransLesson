"""Cross-module checks for independent voices, reference clips and edits."""
from __future__ import annotations

from array import array
import copy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
from pathlib import Path
import threading
import wave

import pytest

from mathbridge.application.coordinator import Coordinator
from mathbridge.application.voices import prepare_reference
from mathbridge.domain.project import file_hash, new_project, segment_fingerprint, validate_project
from mathbridge.storage.project_store import ProjectStore, atomic_json
from mathbridge.worker.__main__ import execute


def wav_bytes(duration=.25, value=300):
    stream = io.BytesIO()
    with wave.open(stream, "wb") as audio:
        audio.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
        audio.writeframes(array("h", [value] * round(duration * 16000)).tobytes())
    return stream.getvalue()


@pytest.fixture
def lesson(tmp_path):
    source = tmp_path / "source.mp4"
    source.write_bytes(b"immutable synthetic media identity")
    project = new_project("多人工程测试", dict(path="inputs/source.link.json", sha256=file_hash(source),
                                               duration_ms=12000, timeline_origin_ms=0))
    project["speakers"] += [dict(id="student", label="学生", voice_mode="standard")]
    for index, speaker in enumerate(("teacher", "student")):
        project["segments"].append(dict(id=f"seg-{index+1}", speaker_id=speaker, start_ms=index*6000,
            end_ms=(index+1)*6000, source_text="Hello, how are you?" if not index else "I am fine, thank you.",
            target_text="你好。" if not index else "我很好。", subtitle_plain="你好。" if not index else "我很好。",
            tts_text="你好。" if not index else "我很好。", formula_ids=[], term_ids=[], review="confirmed"))
    audio = tmp_path / "audio/source-16k.wav"
    audio.parent.mkdir()
    with wave.open(str(audio), "wb") as output:
        output.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
        output.writeframes(array("h", [300] * 96000 + [900] * 96000).tobytes())
    atomic_json(audio.with_suffix(".json"), dict(source_sha256=project["source"]["sha256"], sha256=file_hash(audio)))
    validate_project(project)
    return project, source, audio


def command(lesson, tmp_path, **settings):
    project, source, _ = lesson
    return dict(request_id="req-speakers-test", stage="dub", project_dir=str(tmp_path), project=project,
                project_revision=project["revision"], source_path=str(source), settings=settings,
                options=dict(segment_ids=["seg-1"], sample_only=True), artifacts=[])


def reference_profile(speaker, start=6000, end=10000):
    speaker.update(voice_mode="reference", voice_authorized=True,
        reference_evidence=dict(id="ref-"+speaker["id"], kind="speech", start_ms=start, end_ms=end),
        voice_profile=dict(provider="gpt_sovits", reference_start_ms=start, reference_end_ms=end,
                           reference_text="I am fine, thank you.", reference_language="en", speed=1.0))


def test_each_voice_only_invalidates_its_own_clips(lesson):
    project, _, _ = lesson
    settings = dict(voice="default", voice_clone_model="v1")
    before = [segment_fingerprint(project, s, settings) for s in project["segments"]]
    project["speakers"][1]["voice_profile"] = dict(provider="windows", voice_name="Voice B")
    assert segment_fingerprint(project, project["segments"][0], settings) == before[0]
    assert segment_fingerprint(project, project["segments"][1], settings) != before[1]
    reference_profile(project["speakers"][1])
    changed_model = {**settings, "voice_clone_model": "v2"}
    assert segment_fingerprint(project, project["segments"][0], changed_model) == before[0]
    assert segment_fingerprint(project, project["segments"][1], changed_model) != segment_fingerprint(project, project["segments"][1], settings)


def test_speaker_rename_does_not_require_new_audio(lesson):
    project, _, _ = lesson
    before = segment_fingerprint(project, project["segments"][0], {})
    project["speakers"][0]["label"] = "王老师"
    assert segment_fingerprint(project, project["segments"][0], {}) == before


def test_reference_is_exact_source_interval_and_tampering_is_repaired(lesson, tmp_path):
    project, _, audio = lesson
    reference_profile(project["speakers"][1])
    path = prepare_reference(project, project["speakers"][1], tmp_path, audio)
    with wave.open(str(path)) as reference:
        assert reference.getnframes() == 64000
        assert array("h", reference.readframes(1))[0] == 900
    path.write_bytes(b"tampered")
    prepare_reference(project, project["speakers"][1], tmp_path, audio)
    with wave.open(str(path)) as reference:
        assert reference.getnframes() == 64000


def test_truncated_source_cannot_create_mismatched_reference(lesson, tmp_path):
    project, _, audio = lesson
    reference_profile(project["speakers"][0], 0, 6000)
    audio.write_bytes(audio.read_bytes()[:44 + 4 * 16000 * 2])
    with pytest.raises(ValueError, match="截断"):
        prepare_reference(project, project["speakers"][0], tmp_path, audio)


def test_worker_uses_each_system_voice_and_repairs_bad_cache(lesson, tmp_path, monkeypatch):
    from mathbridge.providers.tts import windows
    project, _, _ = lesson
    project["speakers"][0]["voice_profile"] = dict(provider="windows", voice_name="Voice A")
    project["speakers"][1]["voice_profile"] = dict(provider="windows", voice_name="Voice B")
    calls = []
    def synthesize(text, language, voice, destination, **kwargs):
        calls.append(voice)
        Path(destination).write_bytes(wav_bytes())
    monkeypatch.setattr(windows, "synthesize", synthesize)
    request = command(lesson, tmp_path)
    one = execute(request, threading.Event(), lambda _: None)
    assert one["metadata"]["sample"]
    assert calls == ["Voice A"]
    execute(request, threading.Event(), lambda _: None)
    assert calls == ["Voice A"]
    Path(one["outputs"][0]).with_suffix(".json").write_text("not-json", encoding="utf-8")
    execute(request, threading.Event(), lambda _: None)
    request["options"]["segment_ids"] = ["seg-2"]
    execute(request, threading.Event(), lambda _: None)
    assert calls == ["Voice A", "Voice A", "Voice B"]


def test_worker_sends_selected_speakers_reference_to_local_api(lesson, tmp_path):
    project, source, audio = lesson
    # The timbre reference comes from original media, not the ASR cache. Supply
    # a real source WAV (FFmpeg detects its header despite this fixture suffix).
    source.write_bytes(audio.read_bytes())
    project["source"]["sha256"]=file_hash(source)
    atomic_json(audio.with_suffix(".json"),dict(source_sha256=project["source"]["sha256"],sha256=file_hash(audio)))
    reference_profile(project["speakers"][1])
    payloads = []
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass
        def do_POST(self):
            assert self.path == "/tts"
            payloads.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
            data = wav_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "audio/wav")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        request = command(lesson, tmp_path, voice_clone_api_url=f"http://127.0.0.1:{server.server_port}")
        request["options"]["segment_ids"] = ["seg-2"]
        result = execute(request, threading.Event(), lambda _: None)
        assert len(payloads) == 1
        assert payloads[0]["prompt_text"] == "I am fine, thank you."
        assert payloads[0]["text_lang"] == "zh"
        with wave.open(payloads[0]["ref_audio_path"]) as clip:
            assert clip.getframerate()==32000
            assert array("h", clip.readframes(1))[0] == 900
        with wave.open(result["outputs"][0]) as clip:
            assert clip.getnframes() == 4000
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_manual_split_preserves_evidence_and_is_undoable(lesson, tmp_path):
    project, _, _ = lesson
    project["notes"] = [dict(id="note-1", kind="course", heading="问候", body="你好。",
                             segment_ids=["seg-1"], formula_ids=[], review="confirmed")]
    store = ProjectStore(tmp_path)
    store.save(project)
    coordinator = Coordinator()
    coordinator.project_dir, coordinator.project = tmp_path, copy.deepcopy(project)
    second_id = coordinator.split_segment("seg-1", 3000, 7)
    first, second = coordinator.project["segments"][:2]
    assert first["source_text"] == "Hello,"
    assert second["source_text"] == "how are you?"
    assert first["end_ms"] == second["start_ms"] == 3000
    assert second["id"] == second_id
    assert all(not s["tts_text"] for s in (first, second))
    assert coordinator.project["notes"][0]["segment_ids"] == ["seg-1", second_id]
    assert coordinator.project["notes"][0]["review"] == "needs_review"
    coordinator.undo()
    assert len(coordinator.project["segments"]) == 2
    assert coordinator.project["segments"][0]["source_text"] == "Hello, how are you?"


def test_stale_speaker_analysis_does_not_replace_manual_assignments(lesson, tmp_path):
    project, _, _ = lesson
    coordinator = Coordinator()
    coordinator.project_dir = tmp_path
    coordinator.project = {**copy.deepcopy(project), "revision": 2}
    coordinator.project["segments"][0]["speaker_id"] = "student"
    coordinator._command = dict(stage="diarize", request_id="req-stale-speakers", project_dir=str(tmp_path),
                                project_revision=1, project=project, options={})
    coordinator._receive_result(dict(status="succeeded", result=dict(project=project, outputs=[], metadata={})))
    assert coordinator.project["segments"][0]["speaker_id"] == "student"
    assert ProjectStore(tmp_path).read("runtime.json")["stages"]["diarize"]["status"] == "stale"
