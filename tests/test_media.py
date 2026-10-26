from pathlib import Path
import math
import struct
import sys
import threading
import wave

import pytest

from mathbridge.media.engine import CancelledError, MediaEngine, MediaError
from mathbridge.providers.asr.whisper import transcribe


@pytest.fixture
def engine():
    return MediaEngine(max_frames=3, sample_interval_ms=500)


@pytest.fixture
def source(tmp_path, engine):
    path = tmp_path / "含空格 input.mp4"
    engine._ffmpeg(["-f", "lavfi", "-i", "testsrc2=size=320x180:rate=10:duration=3",
                    "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=3",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", path])
    return path


def tone(path, seconds, rate=24000):
    with wave.open(str(path), "wb") as out:
        out.setparams((1, 2, rate, 0, "NONE", "not compressed"))
        raw = b"".join(struct.pack("<h", round(math.sin(index * 2 * math.pi * 440 / rate) * 5000))
                       for index in range(round(rate * seconds)))
        out.writeframes(raw)
    return path


def test_probe_extract_sparse_frames_and_export(engine, source, tmp_path):
    info = engine.probe(source)
    assert info["width"] == 320 and info["height"] == 180
    assert abs(info["duration_ms"] - 3000) < 100
    assert info["audio_streams"]
    extracted = engine.extract_audio(source, tmp_path / "识别 音频.wav")
    with wave.open(str(extracted)) as audio:
        assert audio.getframerate() == 16000 and audio.getnchannels() == 1
        assert abs(audio.getnframes() - 48000) < 1600
    frames = engine.sample_frames(source, tmp_path / "frames", 3000)
    assert 1 <= len(frames) <= 3
    assert all(Path(frame["path"]).is_file() for frame in frames)
    captions = tmp_path / "双语 字幕.srt"
    captions.write_text("1\n00:00:00,500 --> 00:00:02,500\n考虑 x² + 1。\n", encoding="utf-8")
    mixed = engine.align_and_mix([{"path": tone(tmp_path / "clip.wav", .7), "start_ms": 500, "end_ms": 1500}],
                                  tmp_path / "mixed.wav", 3000)
    output = engine.export_video(source, mixed, captions, tmp_path / "成品 out.mp4")
    exported = engine.probe(output)
    assert abs(exported["duration_ms"] - 3000) <= 300
    assert exported["audio_streams"] and exported["video_streams"]


def test_alignment_keeps_exact_duration_and_silence(engine, tmp_path):
    clip = tone(tmp_path / "one.wav", .25)
    out = engine.align_and_mix([{"path": clip, "start_ms": 500, "end_ms": 1000}], tmp_path / "mix.wav", 2000)
    with wave.open(str(out)) as audio:
        assert audio.getnframes() == 96000
        assert audio.getframerate() == 48000
        assert audio.getnchannels() == 2
        assert not any(audio.readframes(20000))
        audio.setpos(26000)
        assert any(audio.readframes(1000))
        audio.setpos(60000)
        assert not any(audio.readframes(20000))


def test_tts_mild_speedup_and_no_truncation(engine, tmp_path):
    clip = tone(tmp_path / "long.wav", 1.1)
    out = engine.align_and_mix([{"path": clip, "start_ms": 0, "end_ms": 1000}], tmp_path / "fit.wav", 2000)
    assert out.is_file()
    too_long = tone(tmp_path / "too-long.wav", 1.5)
    with pytest.raises(MediaError, match="TTS_TOO_LONG"):
        engine.align_and_mix([{"path": too_long, "start_ms": 0, "end_ms": 1000}], tmp_path / "rejected.wav", 2000)
    assert not (tmp_path / "rejected.wav").exists()


def test_overlap_rejected(engine, tmp_path):
    clip = tone(tmp_path / "clip.wav", .1)
    with pytest.raises(MediaError, match="INVALID_TIMELINE"):
        engine.align_and_mix([{"path": clip, "start_ms": 0, "end_ms": 700},
                              {"path": clip, "start_ms": 500, "end_ms": 1000}], tmp_path / "bad.wav", 2000)


def test_cancel_retains_existing_output(tmp_path):
    cancelled = threading.Event()
    cancelled.set()
    engine = MediaEngine(cancel=cancelled)
    output = tmp_path / "existing.wav"
    output.write_bytes(b"keep")
    with pytest.raises(CancelledError):
        engine.align_and_mix([], output, 2000)
    assert output.read_bytes() == b"keep"


def test_asr_requires_complete_local_model_before_import(tmp_path):
    audio = tone(tmp_path / "source.wav", .1)
    with pytest.raises(MediaError, match="MISSING_MODEL"):
        transcribe(audio, "zh-CN", tmp_path / "missing-model")


def test_extracted_delayed_audio_keeps_leading_silence(engine, tmp_path):
    source = tmp_path / "delayed.mp4"
    clip = tone(tmp_path / "late-tone.wav", 1)
    engine._ffmpeg(["-f", "lavfi", "-i", "color=c=black:size=160x90:rate=10:duration=3",
                    "-itsoffset", "1", "-i", clip, "-c:v", "libx264", "-c:a", "aac", source])
    extracted = engine.extract_audio(source, tmp_path / "delayed-extract.wav")
    with wave.open(str(extracted)) as audio:
        samples = struct.unpack("<" + "h" * 12000, audio.readframes(12000))
        assert max(abs(sample) for sample in samples) < 20
        audio.setpos(20000)
        samples = struct.unpack("<" + "h" * 2000, audio.readframes(2000))
        assert max(abs(sample) for sample in samples) > 1000


def test_preview_is_actual_short_clip(engine, source, tmp_path):
    captions = tmp_path / "preview.srt"
    captions.write_text("1\n00:00:00,000 --> 00:00:01,000\nShort preview\n", encoding="utf-8")
    audio = engine.align_and_mix([], tmp_path / "short.wav", 1000)
    output = engine.preview_video(source, audio, captions, tmp_path / "short.mp4", duration_ms=1000)
    info = engine.probe(output)
    assert abs(info["duration_ms"] - 1000) <= 300
    assert info["video_streams"] and info["audio_streams"] and info["subtitle_streams"]
    extracted_subtitles = tmp_path / "roundtrip.srt"
    engine._ffmpeg(["-i", output, "-map", "0:s:0", extracted_subtitles])
    subtitle_text = extracted_subtitles.read_text(encoding="utf-8")
    assert "Short preview" in subtitle_text and "00:00:01,000" in subtitle_text
    with pytest.raises(MediaError, match="TTS_TOO_LONG"):
        engine.preview_video(source, tone(tmp_path / "full.wav", 2), captions,
                             tmp_path / "cut.mp4", duration_ms=1000)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows installed voices only")
def test_windows_real_wave(tmp_path):
    from mathbridge.providers.tts.windows import list_voices, synthesize
    voices = list_voices()
    if not voices:
        pytest.skip("This Windows installation exposes no System.Speech voice")
    voice = next((v for v in voices if v["language"].lower().startswith("en")), voices[0])
    text = "Consider x squared plus one." if voice["language"].lower().startswith("en") else "考虑 x 的平方加一。"
    try:
        output = synthesize(text, voice["language"], voice, tmp_path / "真实 语音.wav", rate=1)
    except MediaError as exc:
        if "0x80045040" in str(exc) or "0x80070005" in str(exc):
            pytest.skip("SAPI synthesis is blocked by this execution context; run this integration test outside the sandbox")
        raise
    with wave.open(str(output)) as audio:
        assert audio.getframerate() == 24000 and audio.getnframes() > 2400
