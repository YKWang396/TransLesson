"""Checkpoint behavior with deterministic provider output; no weights/network."""
import hashlib
import json
import os
import sys
import threading
from types import ModuleType, SimpleNamespace

import pytest

from mathbridge.media.engine import CancelledError
from mathbridge.providers.asr.whisper import transcribe


def segment(start, end, text):
    return SimpleNamespace(start=start, end=end, text=text, avg_logprob=-.2,
                           no_speech_prob=.01,
                           words=[SimpleNamespace(start=start, end=end, word=text,
                                                  probability=.95)])


@pytest.fixture
def local_provider(tmp_path, monkeypatch):
    audio = tmp_path / "audio.wav"
    audio.write_bytes(b"audio-one")
    model = tmp_path / "model"
    model.mkdir()
    for name in ("model.bin", "config.json", "tokenizer.json"):
        (model / name).write_bytes(b"weights1")
    state = SimpleNamespace(calls=[], loads=0, cancel_during_yield=None,
                            records=[segment(1., 2., "First original sentence."),
                                     segment(3., 4., "Second original sentence.")])

    class FakeWhisperModel:
        def __init__(self, *args, **kwargs):
            assert kwargs["local_files_only"] is True
            state.loads += 1
            self.model = SimpleNamespace(is_multilingual=True)

        def transcribe(self, audio, *, clip_timestamps=None, **kwargs):
            state.calls.append(clip_timestamps)
            records = [record for record in state.records
                       if clip_timestamps is None or record.start >= clip_timestamps[0]]

            def generate():
                for record in records:
                    if state.cancel_during_yield is not None:
                        state.cancel_during_yield.set()
                    yield record

            return generate(), SimpleNamespace(duration=6.)

    module = ModuleType("faster_whisper")
    module.WhisperModel = FakeWhisperModel
    monkeypatch.setitem(sys.modules, "faster_whisper", module)
    return audio, model, tmp_path / "raw.json", state


def partial(local_provider):
    audio, model, output, state = local_provider
    cancelled = threading.Event()
    state.cancel_during_yield = cancelled
    with pytest.raises(CancelledError):
        transcribe(audio, "en", model, cancel=cancelled, raw_output=output)
    state.cancel_during_yield = None
    return json.loads(output.read_text(encoding="utf-8"))


def test_cancel_mid_inference_commits_yielded_unit_and_resume_has_original_times(local_provider):
    audio, model, output, state = local_provider
    saved = partial(local_provider)
    assert saved["complete"] is False
    assert len(saved["segments"]) == 1
    assert saved["segments"][0]["words"][0]["word"] == "First original sentence."
    assert saved["resume_from_seconds"] == 2.
    assert saved["inputs"]["audio_sha256"] == hashlib.sha256(audio.read_bytes()).hexdigest()
    assert len(saved["input_fingerprint"]) == 64
    assert {file["path"] for file in saved["inputs"]["model_files"]} == {"model.bin", "config.json", "tokenizer.json"}
    assert saved["inputs"]["provider_version"]
    assert not list(output.parent.glob(".*.tmp"))

    resumed = transcribe(audio, "en", model, raw_output=output)
    assert state.calls == [None, [2.]]
    assert [row["id"] for row in resumed] == ["seg-1", "seg-2"]
    assert resumed[0]["source_text"] == "First original sentence."
    assert resumed[1]["start_ms"] == 3000  # clip output already uses original audio time
    assert resumed[1]["end_ms"] == 4000
    assert json.loads(output.read_text(encoding="utf-8"))["complete"] is True


def test_complete_checkpoint_reused_without_loading_model(local_provider):
    audio, model, output, state = local_provider
    first = transcribe(audio, "en", model, raw_output=output)
    assert state.loads == 1
    second = transcribe(audio, "en", model, raw_output=output)
    assert second == first
    assert state.loads == 1 and state.calls == [None]


@pytest.mark.parametrize("damage", ["malformed-json", "altered-result", "invalid-timeline"])
def test_corrupt_checkpoint_is_preserved_and_never_resumed(local_provider, damage):
    audio, model, output, state = local_provider
    saved = partial(local_provider)
    if damage == "malformed-json":
        damaged_bytes = b"{this is incomplete JSON"
    else:
        saved["segments"][0]["end"] = -100
        if damage == "invalid-timeline":
            # Even a JSON/checksum-consistent file must pass semantic validation.
            saved.pop("checkpoint_checksum")
            serialized = json.dumps(saved, sort_keys=True, ensure_ascii=False,
                                    separators=(",", ":"), allow_nan=False)
            saved["checkpoint_checksum"] = hashlib.sha256(serialized.encode("utf-8")).hexdigest()
        damaged_bytes = json.dumps(saved).encode("utf-8")
    output.write_bytes(damaged_bytes)
    result = transcribe(audio, "en", model, raw_output=output)
    assert len(result) == 2
    assert state.calls == [None, None]
    rejected = list(output.parent.glob("raw.rejected-*.json"))
    assert len(rejected) == 1 and rejected[0].read_bytes() == damaged_bytes
    assert json.loads(output.read_text(encoding="utf-8"))["complete"] is True


@pytest.mark.parametrize("changed_input", ["audio", "model", "language"])
def test_input_fingerprint_invalidates_prefix_by_content(local_provider, changed_input):
    audio, model, output, state = local_provider
    previous = partial(local_provider)
    language = "en"
    if changed_input in ("audio", "model"):
        path = audio if changed_input == "audio" else model / "model.bin"
        info = path.stat()
        replacement = b"audio-two" if changed_input == "audio" else b"weights2"
        assert len(replacement) == info.st_size
        path.write_bytes(replacement)
        os.utime(path, ns=(info.st_atime_ns, info.st_mtime_ns))
    else:
        language = "zh-CN"
    result = transcribe(audio, language, model, raw_output=output)
    current = json.loads(output.read_text(encoding="utf-8"))
    assert len(result) == 2 and state.calls == [None, None]
    assert current["input_fingerprint"] != previous["input_fingerprint"]
    assert len(list(output.parent.glob("raw.rejected-*.json"))) == 1
