"""Offline speaker assignment contracts; deterministic tests need no weights."""
import copy
import sys
import threading
from types import ModuleType, SimpleNamespace
import wave

import pytest

from mathbridge.domain.project import new_project, validate_project
from mathbridge.media.engine import CancelledError, MediaError
from mathbridge.providers.diarization import apply_diarization, diarize


def lesson():
    project = new_project("双人课程", {"path": "source/video.mp4", "sha256": "a" * 64,
                                        "duration_ms": 10000, "timeline_origin_ms": 0})
    project["segments"] = [{"id": "seg-1", "speaker_id": "teacher", "start_ms": 0,
                            "end_ms": 6000, "source_text": "Hello there. Good morning.",
                            "target_text": "", "subtitle_plain": "", "tts_text": "",
                            "formula_ids": [], "term_ids": [], "review": "needs_review"}]
    return project


def turns():
    return [{"speaker_id": "teacher", "start_ms": 0, "end_ms": 2900},
            {"speaker_id": "speaker-2", "start_ms": 3100, "end_ms": 6000}]


def words():
    return [{"segment_id": "seg-1", "text": "Hello there. Good morning.", "start": 0., "end": 6.,
             "words": [{"start": 0., "end": 1., "word": "Hello"},
                       {"start": 1.1, "end": 2., "word": " there."},
                       {"start": 3.2, "end": 4., "word": " Good"},
                       {"start": 4.1, "end": 5., "word": " morning."}]}]


def test_word_level_switch_splits_preserves_text_and_never_overlaps():
    project = lesson()
    before = copy.deepcopy(project)
    candidate, warnings = apply_diarization(project, turns(), {"segments": words()})
    assert project == before
    assert candidate["revision"] == project["revision"]
    assert [s["speaker_id"] for s in candidate["segments"]] == ["teacher", "speaker-2"]
    assert [s["source_text"] for s in candidate["segments"]] == ["Hello there.", "Good morning."]
    first, second = candidate["segments"]
    assert first["id"] == "seg-1" and second["id"] != "seg-1"
    assert first["start_ms"] == 0 and first["end_ms"] == second["start_ms"]
    assert second["end_ms"] == 6000
    assert all(s["review"] == "needs_review" for s in candidate["segments"])
    assert "拆为 2 段" in " ".join(warnings)
    validate_project(candidate)


@pytest.mark.parametrize("change", ["no-words", "edited-text", "edited-time", "incomplete-words", "overlap-words"])
def test_missing_or_stale_word_evidence_never_guesses_text_splits(change):
    project = lesson()
    raw = words()
    if change == "no-words":
        raw = None
    elif change == "edited-text":
        project["segments"][0]["source_text"] += " Corrected."
    elif change == "edited-time":
        project["segments"][0]["end_ms"] = 6100
    elif change == "incomplete-words":
        raw[0]["words"].pop()
    elif change == "overlap-words":
        raw[0]["words"][2]["start"] = 1.9
    candidate, warnings = apply_diarization(project, turns(), raw)
    assert len(candidate["segments"]) == 1
    assert candidate["segments"][0]["source_text"] == project["segments"][0]["source_text"]
    assert "同段包含多位说话人" in " ".join(warnings)


@pytest.mark.parametrize("protect", ["translated", "confirmed", "note"])
def test_translations_and_references_are_preserved(protect):
    project = lesson()
    segment = project["segments"][0]
    if protect in ("translated", "confirmed"):
        segment.update(target_text="你好。早上好。", subtitle_plain="你好。早上好。", tts_text="你好。早上好。")
        if protect == "confirmed":
            segment["review"] = "confirmed"
    else:
        project["notes"] = [{"id": "note-1", "kind": "course", "heading": "问候",
                             "body": "问候语。", "segment_ids": ["seg-1"],
                             "formula_ids": [], "review": "confirmed"}]
    candidate, warnings = apply_diarization(project, turns(), words())
    assert len(candidate["segments"]) == 1
    assert candidate["segments"][0]["source_text"] == segment["source_text"]
    assert candidate["segments"][0]["target_text"] == segment["target_text"]
    assert candidate["segments"][0]["review"] == "needs_review"
    if protect == "note":
        assert candidate["notes"][0]["segment_ids"] == ["seg-1"]
        assert candidate["notes"][0]["review"] == "needs_review"
    assert warnings
    validate_project(candidate)


def test_simultaneous_speakers_are_explicitly_flagged_and_not_cut_into_subtitles():
    overlapping = turns()
    overlapping[0]["end_ms"] = 4000
    candidate, warnings = apply_diarization(lesson(), overlapping, words())
    assert len(candidate["segments"]) == 1
    assert "多人同时讲话" in " ".join(warnings)
    validate_project(candidate)


def test_no_coverage_keeps_original_owner_and_flags_review():
    project = lesson()
    later = [{"speaker_id": "speaker-2", "start_ms": 7000, "end_ms": 9000}]
    candidate, warnings = apply_diarization(project, later)
    assert candidate["segments"][0]["speaker_id"] == "teacher"
    assert "没有检测到对应声线" in " ".join(warnings)
    assert apply_diarization(project, [])[0] == project


def test_sparse_voice_evidence_is_not_treated_as_confirmed():
    project = lesson()
    project["segments"][0].update(target_text="你好。", subtitle_plain="你好。", tts_text="你好。", review="confirmed")
    candidate, warnings = apply_diarization(project, [{"speaker_id": "teacher", "start_ms": 0, "end_ms": 100}])
    assert candidate["segments"][0]["review"] == "needs_review"
    assert "覆盖不足一半" in " ".join(warnings)


def test_speaker_voice_settings_remain_and_split_ids_do_not_collide():
    project = lesson()
    project["speakers"].append({"id": "speaker-2", "label": "学生", "voice_mode": "standard"})
    last = copy.deepcopy(project["segments"][0])
    last.update(id="seg-1-spk-2", start_ms=8000, end_ms=9000)
    project["segments"].append(last)
    candidate, _ = apply_diarization(project, turns(), words())
    assert candidate["speakers"] == project["speakers"]
    assert candidate["segments"][1]["id"] == "seg-1-spk-3"
    validate_project(candidate)


@pytest.mark.parametrize("invalid", [
    [{"speaker_id": "bad/id", "start_ms": 0, "end_ms": 100}],
    [{"speaker_id": "teacher", "start_ms": 1.5, "end_ms": 100}],
    [{"speaker_id": "teacher", "start_ms": False, "end_ms": 100}],
    [{"speaker_id": "teacher", "start_ms": 0, "end_ms": 10001}],
    [{"speaker_id": "teacher", "start_ms": 100, "end_ms": 100}],
])
def test_invalid_turns_are_rejected(invalid):
    with pytest.raises(MediaError, match="INVALID_DIARIZATION"):
        apply_diarization(lesson(), invalid)


@pytest.fixture
def fake_backend(tmp_path, monkeypatch):
    audio = tmp_path / "audio.wav"
    with wave.open(str(audio), "wb") as output:
        output.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
        output.writeframes(b"\x00\x00" * 16000 * 6)
    segmentation, embedding = tmp_path / "seg.onnx", tmp_path / "embedding.onnx"
    segmentation.write_bytes(b"local segmentation")
    embedding.write_bytes(b"local embedding")
    state = SimpleNamespace(configs=[], calls=0, cancel=None)
    module = ModuleType("sherpa_onnx")

    class Config(SimpleNamespace):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            state.configs.append(self)

        def validate(self):
            return True

    class Pipeline:
        sample_rate = 16000

        def __init__(self, config):
            self.config = config

        def process(self, samples, callback):
            state.calls += 1
            assert samples.dtype.name == "float32" and len(samples) == 16000 * 6
            if state.cancel is not None:
                state.cancel.set()
                assert callback(1, 2) == 1
                values = []
            else:
                assert callback(1, 2) == 0
                values = [SimpleNamespace(speaker=18, start=3., end=5.),
                          SimpleNamespace(speaker=7, start=1., end=2.)]
            return SimpleNamespace(sort_by_start_time=lambda: values)

    for name in ("OfflineSpeakerDiarizationConfig", "OfflineSpeakerSegmentationModelConfig",
                 "OfflineSpeakerSegmentationPyannoteModelConfig", "SpeakerEmbeddingExtractorConfig",
                 "FastClusteringConfig"):
        setattr(module, name, Config)
    module.OfflineSpeakerDiarization = Pipeline
    monkeypatch.setitem(sys.modules, "sherpa_onnx", module)
    return audio, segmentation, embedding, state


def test_local_models_cpu_parameters_and_ordered_ids(fake_backend):
    audio, segmentation, embedding, state = fake_backend
    messages = []
    result = diarize(audio, segmentation, embedding, num_speakers=2,
                     progress=lambda *args: messages.append(args))
    assert result == [{"speaker_id": "teacher", "start_ms": 1000, "end_ms": 2000},
                      {"speaker_id": "speaker-2", "start_ms": 3000, "end_ms": 5000}]
    config = state.configs[-1]
    assert config.segmentation.provider == config.embedding.provider == "cpu"
    assert config.clustering.num_clusters == 2
    assert config.segmentation.pyannote.model == str(segmentation)
    assert messages[-1][0] == "diarize"


def test_callback_cancellation_does_not_accept_partial_turns(fake_backend):
    audio, segmentation, embedding, state = fake_backend
    state.cancel = threading.Event()
    with pytest.raises(CancelledError):
        diarize(audio, segmentation, embedding, cancel=state.cancel)
    assert state.calls == 1


def test_model_missing_is_rejected_before_provider_load(fake_backend):
    audio, segmentation, embedding, state = fake_backend
    segmentation.unlink()
    with pytest.raises(MediaError, match="MISSING_MODEL"):
        diarize(audio, segmentation, embedding)
    assert state.calls == 0 and not state.configs


def test_noncanonical_audio_rejected(fake_backend):
    audio, segmentation, embedding, state = fake_backend
    with wave.open(str(audio), "wb") as output:
        output.setparams((1, 2, 8000, 0, "NONE", "not compressed"))
        output.writeframes(b"\x00\x00" * 8000)
    with pytest.raises(MediaError, match="INVALID_AUDIO"):
        diarize(audio, segmentation, embedding)
    assert state.calls == 0
