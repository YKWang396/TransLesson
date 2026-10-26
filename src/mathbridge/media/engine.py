"""Bounded-memory media processing. Every subprocess uses an argument list.

FFprobe is preferred. The explicitly reported FFmpeg stderr fallback can inspect
ordinary files, but cannot provide authoritative rotation/VFR/PTS metadata.
"""
from __future__ import annotations

from collections import deque
from contextlib import contextmanager
import json
import math
import os
from pathlib import Path
import queue
import re
import shutil
import subprocess
import tempfile
import threading
import uuid
import wave


class MediaError(RuntimeError):
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(f"{code}: {message}")


class CancelledError(MediaError):
    def __init__(self):
        super().__init__("CANCELLED", "任务已取消，临时产物未提交。")


def _seconds(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


class MediaEngine:
    def __init__(self, ffmpeg_path=None, ffprobe_path=None, cancel=None,
                 progress=None, *, max_frames=120, sample_interval_ms=2000):
        if not ffmpeg_path:
            ffmpeg_path = shutil.which("ffmpeg")
        if not ffmpeg_path:
            try:
                import imageio_ffmpeg
                ffmpeg_path = imageio_ffmpeg.get_ffmpeg_exe()
            except (ImportError, RuntimeError):
                raise MediaError("MISSING_DEPENDENCY", "未找到 FFmpeg，请安装媒体工具。")
        self.ffmpeg_path = str(ffmpeg_path)
        self.ffprobe_path = str(ffprobe_path or shutil.which("ffprobe") or "")
        if not self.ffprobe_path:
            sibling = Path(self.ffmpeg_path).with_name("ffprobe.exe" if os.name == "nt" else "ffprobe")
            if sibling.is_file():
                self.ffprobe_path = str(sibling)
        self.cancel = cancel
        self.progress = progress
        self.max_frames = max(1, int(max_frames))
        self.sample_interval_ms = max(250, int(sample_interval_ms))

    def _check(self):
        if self.cancel is not None and self.cancel.is_set():
            raise CancelledError()

    def _report(self, stage, completed, total, message=""):
        if self.progress:
            self.progress(stage, completed, total, message)

    @staticmethod
    def _source(path):
        path = Path(path).resolve()
        if not path.is_file():
            raise MediaError("SOURCE_MISSING", f"文件不存在：{path}")
        return path

    @staticmethod
    def _stop(proc):
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()

    def _run(self, args, *, stage="media", duration_ms=None, allow_failure=False, cwd=None):
        self._check()
        flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        try:
            proc = subprocess.Popen([str(a) for a in args], stdout=subprocess.PIPE,
                                    stderr=subprocess.PIPE, stdin=subprocess.DEVNULL,
                                    creationflags=flags, cwd=cwd)
        except OSError as exc:
            raise MediaError("MISSING_DEPENDENCY", str(exc)) from exc
        stdout, stderr = deque(maxlen=20000), deque(maxlen=2000)
        events = queue.Queue()

        def drain(stream, target, parse=False):
            for raw in iter(stream.readline, b""):
                line = raw.decode("utf-8", errors="replace").rstrip()
                target.append(line)
                if parse and line.startswith("out_time_us="):
                    try:
                        events.put(int(line.split("=", 1)[1]) // 1000)
                    except ValueError:
                        pass

        readers = [threading.Thread(target=drain, args=(proc.stdout, stdout, True), daemon=True),
                   threading.Thread(target=drain, args=(proc.stderr, stderr), daemon=True)]
        for reader in readers:
            reader.start()
        try:
            while proc.poll() is None:
                self._check()
                try:
                    done = events.get(timeout=.1)
                    if duration_ms:
                        self._report(stage, min(done, duration_ms), duration_ms)
                except queue.Empty:
                    pass
            self._check()
        except BaseException:
            self._stop(proc)
            raise
        finally:
            for reader in readers:
                reader.join(timeout=3)
            proc.stdout.close()
            proc.stderr.close()
        out, err = "\n".join(stdout), "\n".join(stderr)
        if proc.returncode and not allow_failure:
            code = "DISK_FULL" if "No space left" in err else "UNSUPPORTED_MEDIA"
            raise MediaError(code, err[-5000:] or f"媒体工具退出码 {proc.returncode}")
        if duration_ms and not proc.returncode:
            self._report(stage, duration_ms, duration_ms)
        return proc.returncode, out, err

    def _ffmpeg(self, args, *, stage="media", duration_ms=None, cwd=None):
        return self._run([self.ffmpeg_path, "-hide_banner", "-nostdin", "-y",
                          "-loglevel", "error", "-nostats", "-progress", "pipe:1", *args],
                         stage=stage, duration_ms=duration_ms, cwd=cwd)

    @contextmanager
    def _target(self, destination):
        destination = Path(destination).resolve()
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_name(f".{destination.stem}-{uuid.uuid4().hex}.tmp{destination.suffix}")
        try:
            yield temporary
            self._check()
            if not temporary.is_file() or temporary.stat().st_size == 0:
                raise MediaError("UNSUPPORTED_MEDIA", "媒体工具未生成有效产物。")
            os.replace(temporary, destination)
        finally:
            temporary.unlink(missing_ok=True)

    def probe(self, source: Path) -> dict:
        source = self._source(source)
        if self.ffprobe_path:
            _, out, _ = self._run([self.ffprobe_path, "-v", "error", "-show_format",
                                  "-show_streams", "-of", "json", source])
            try:
                data = json.loads(out)
            except json.JSONDecodeError as exc:
                raise MediaError("UNSUPPORTED_MEDIA", "FFprobe 返回了无效元数据。") from exc
            fmt = data.get("format", {})
            streams = data.get("streams", [])
            video = [s for s in streams if s.get("codec_type") == "video" and not s.get("disposition", {}).get("attached_pic")]
            audio = [s for s in streams if s.get("codec_type") == "audio"]
            subtitles = [s for s in streams if s.get("codec_type") == "subtitle"]
            first = video[0] if video else {}
            duration = _seconds(first.get("duration")) or _seconds(fmt.get("duration"))
            origin = _seconds(fmt.get("start_time"))
            rotation = first.get("tags", {}).get("rotate", 0)
            for side in first.get("side_data_list", []):
                rotation = side.get("rotation", rotation)
            return {"duration_ms": round(duration * 1000), "timeline_origin_ms": round(origin * 1000),
                    "width": first.get("width", 0), "height": first.get("height", 0),
                    "audio_streams": audio, "video_streams": video, "subtitle_streams": subtitles,
                    "rotation": int(float(rotation)),
                    "vfr": first.get("avg_frame_rate") != first.get("r_frame_rate"),
                    "format": fmt.get("format_name", ""), "probe_backend": "ffprobe", "warnings": []}
        _, _, err = self._run([self.ffmpeg_path, "-hide_banner", "-nostdin", "-i", source], allow_failure=True)
        duration = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", err)
        if not duration:
            raise MediaError("UNSUPPORTED_MEDIA", f"无法探测媒体时长：{err[-2000:]}")
        hours, minutes, seconds = map(float, duration.groups())
        origin = re.search(r"start:\s*(-?\d+(?:\.\d+)?)", err)
        video, audio, subtitles = [], [], []
        for line in err.splitlines():
            match = re.search(r"Stream #\d+:(\d+)(?:\[[^]]+\])?(?:\([^)]*\))?: (Video|Audio|Subtitle):\s*([^,\s]+)", line)
            if not match:
                continue
            stream = {"index": int(match[1]), "codec_name": match[3], "codec_type": match[2].lower()}
            if match[2] == "Video":
                size = re.search(r"\b(\d{2,6})x(\d{2,6})\b", line)
                if size:
                    stream.update(width=int(size[1]), height=int(size[2]))
                video.append(stream)
            elif match[2] == "Audio":
                hz = re.search(r"(\d+) Hz", line)
                stream["sample_rate"] = int(hz[1]) if hz else 0
                stream["channels"] = 1 if "mono" in line else 2 if "stereo" in line else None
                audio.append(stream)
            else:
                subtitles.append(stream)
        first = video[0] if video else {}
        return {"duration_ms": round((hours * 3600 + minutes * 60 + seconds) * 1000),
                "timeline_origin_ms": round(float(origin[1]) * 1000) if origin else 0,
                "width": first.get("width", 0), "height": first.get("height", 0),
                "audio_streams": audio, "video_streams": video, "subtitle_streams": subtitles,
                "rotation": None, "vfr": None,
                "format": source.suffix.lstrip("."), "probe_backend": "ffmpeg-stderr",
                "warnings": ["FFPROBE_UNAVAILABLE: FFmpeg 降级探测，无法完整确认旋转、可变帧率及各轨起始PTS。"]}

    def extract_audio(self, source, dest) -> Path:
        source = self._source(source)
        info = self.probe(source)
        if not info["audio_streams"]:
            raise MediaError("UNSUPPORTED_MEDIA", "源媒体没有可用音轨。")
        duration = info["duration_ms"]
        with self._target(dest) as temporary:
            self._ffmpeg(["-i", source, "-map", "0:a:0", "-vn", "-af",
                          f"aresample=16000:async=1:first_pts=0,apad,atrim=duration={duration / 1000:.6f}",
                          "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", temporary],
                         stage="extract_audio", duration_ms=duration)
        return Path(dest).resolve()

    def frame_at(self, source, dest, time_ms) -> Path:
        source = self._source(source)
        if time_ms < 0:
            raise MediaError("INVALID_TIMELINE", "帧时间不能小于零。")
        with self._target(dest) as temporary:
            self._ffmpeg(["-ss", f"{time_ms / 1000:.6f}", "-i", source,
                          "-map", "0:v:0", "-frames:v", "1", "-update", "1", temporary], stage="frames")
        return Path(dest).resolve()

    def sample_frames(self, source, output_dir, duration_ms) -> list[dict]:
        """Stream 160x90 grayscale thumbnails; retain bounded, spaced evidence.

        At most ``max_frames`` original-resolution PNGs are saved. Thumbnail
        sampling detects slide changes plus a ten-second fallback, with spacing
        enlarged for long lessons. Exact output timestamps come from the fps
        filter's normalized timestamp grid, never from source frame counts.
        """
        source = self._source(source)
        if duration_ms <= 0:
            raise MediaError("INVALID_TIMELINE", "视频时长必须大于零。")
        output_dir = Path(output_dir).resolve()
        output_dir.mkdir(parents=True, exist_ok=True)
        interval = self.sample_interval_ms
        args = [self.ffmpeg_path, "-hide_banner", "-nostdin", "-loglevel", "error", "-i", str(source),
                "-an", "-vf", f"fps=1000/{interval}:start_time=0,scale=160:90,format=gray",
                "-f", "rawvideo", "-pix_fmt", "gray", "pipe:1"]
        proc = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                stdin=subprocess.DEVNULL,
                                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        frames = queue.Queue(maxsize=4)
        errors = deque(maxlen=50)
        reader_stop = threading.Event()

        def enqueue(value):
            while not reader_stop.is_set():
                try:
                    frames.put(value, timeout=.1)
                    return
                except queue.Full:
                    continue

        def read_frames():
            try:
                while True:
                    raw = proc.stdout.read(160 * 90)
                    if len(raw) != 160 * 90:
                        break
                    enqueue(raw)
                    if reader_stop.is_set():
                        return
            finally:
                enqueue(None)

        def read_errors():
            for raw in iter(proc.stderr.readline, b""):
                errors.append(raw.decode("utf-8", errors="replace"))

        stdout_reader = threading.Thread(target=read_frames, daemon=True)
        stdout_reader.start()
        stderr_reader = threading.Thread(target=read_errors, daemon=True)
        stderr_reader.start()
        times, previous, index = [], None, 0
        spacing = max(interval, math.ceil(duration_ms / self.max_frames))
        fallback = max(10000, spacing)
        try:
            while True:
                self._check()
                try:
                    raw = frames.get(timeout=.15)
                except queue.Empty:
                    if proc.poll() not in (None, 0):
                        raise MediaError("UNSUPPORTED_MEDIA", "".join(errors)[-3000:])
                    continue
                if raw is None:
                    break
                timestamp = min(index * interval, duration_ms - 1)
                changed = previous is None or sum(abs(a - b) for a, b in zip(raw, previous)) / len(raw) > 10
                gap = timestamp - times[-1] if times else duration_ms
                if len(times) < self.max_frames and (not times or (gap >= spacing and (changed or gap >= fallback))):
                    times.append(timestamp)
                previous = raw
                index += 1
                self._report("frames", min(timestamp, duration_ms), duration_ms, "检查画面变化")
            if proc.wait(timeout=10):
                raise MediaError("UNSUPPORTED_MEDIA", "".join(errors)[-3000:])
        finally:
            reader_stop.set()
            self._stop(proc)
            stdout_reader.join(timeout=2)
            proc.stdout.close()
            stderr_reader.join(timeout=2)
            proc.stderr.close()
        if not times:
            raise MediaError("UNSUPPORTED_MEDIA", "媒体未产生可解码画面。")
        result = []
        for index, timestamp in enumerate(times):
            self._check()
            path = self.frame_at(source, output_dir / f"frame-{timestamp:010d}.png", timestamp)
            result.append({"path": str(path), "start_ms": timestamp, "end_ms": timestamp,
                           "bbox_xyxy": [0.0, 0.0, 1.0, 1.0]})
            self._report("frames", index + 1, len(times), "保存原分辨率画面证据")
        return result

    def _audio_duration(self, path):
        try:
            with wave.open(str(path), "rb") as audio:
                return audio.getnframes() / audio.getframerate()
        except (wave.Error, EOFError):
            return self.probe(path)["duration_ms"] / 1000

    def align_and_mix(self, clips, dest, duration_ms, source=None, background="dub_only") -> Path:
        background = {"quiet_original": "low_original", "original": "original_only"}.get(background, background)
        if background not in ("dub_only", "low_original", "original_only"):
            raise MediaError("BACKGROUND_UNAVAILABLE", "仅支持译声、低音量原声旁白或保留原声。")
        if duration_ms <= 0:
            raise MediaError("INVALID_TIMELINE", "输出时长必须大于零。")
        if background != "dub_only" and source is None:
            raise MediaError("SOURCE_MISSING", "所选背景策略需要原视频。")
        dest = Path(dest).resolve()
        dest.parent.mkdir(parents=True, exist_ok=True)
        rate, channels, width = 48000, 2, 2
        total_frames = round(duration_ms * rate / 1000)
        ordered = sorted(clips, key=lambda c: c["start_ms"])
        previous_end = 0
        for clip in ordered:
            start, end = clip["start_ms"], clip["end_ms"]
            if not (0 <= start < end <= duration_ms) or start < previous_end:
                raise MediaError("INVALID_TIMELINE", "配音区间越界或重叠。")
            self._source(clip["path"])
            previous_end = end
        with tempfile.TemporaryDirectory(prefix="mathbridge-mix-", dir=dest.parent) as folder:
            folder = Path(folder)
            aligned = folder / "aligned.wav"
            with wave.open(str(aligned), "wb") as output:
                output.setparams((channels, width, rate, 0, "NONE", "not compressed"))
                written = 0

                def silence(count):
                    while count > 0:
                        self._check()
                        size = min(count, rate)
                        output.writeframesraw(bytes(size * channels * width))
                        count -= size

                for index, clip in enumerate(ordered):
                    self._check()
                    start_frame = round(clip["start_ms"] * rate / 1000)
                    window = (clip["end_ms"] - clip["start_ms"]) / 1000
                    original_duration = self._audio_duration(clip["path"])
                    speed = max(1.0, original_duration / window)
                    if speed > 1.15 + 1e-8:
                        raise MediaError("TTS_TOO_LONG", f"第 {index + 1} 段配音需 {speed:.2f} 倍速，请缩短读法或调整分段。")
                    normalized = folder / f"clip-{index}.wav"
                    for attempt in range(2):
                        self._ffmpeg(["-i", clip["path"], "-vn", "-af", f"atempo={speed:.9f}",
                                      "-ar", str(rate), "-ac", str(channels), "-c:a", "pcm_s16le", normalized], stage="mix")
                        with wave.open(str(normalized), "rb") as audio:
                            count = audio.getnframes()
                        capacity = round(window * rate)
                        if count <= capacity:
                            break
                        speed = max(speed * count / capacity * 1.001, speed + .001)
                        if speed > 1.15 or attempt == 1:
                            raise MediaError("TTS_TOO_LONG", f"第 {index + 1} 段配音仍放不下，已保留完整音频，请调整读法。")
                    silence(start_frame - written)
                    with wave.open(str(normalized), "rb") as audio:
                        while raw := audio.readframes(rate):
                            self._check()
                            output.writeframesraw(raw)
                    written = start_frame + count
                    self._report("mix", index + 1, len(ordered), "对齐配音片段")
                silence(total_frames - written)
            with self._target(dest) as temporary:
                if background == "dub_only":
                    if dest.suffix.lower() == ".wav":
                        with aligned.open("rb") as reader, temporary.open("wb") as writer:
                            while raw := reader.read(1024 * 1024):
                                self._check()
                                writer.write(raw)
                    else:
                        self._ffmpeg(["-i", aligned, temporary], stage="mix", duration_ms=duration_ms)
                else:
                    source = self._source(source)
                    if background == "original_only":
                        self._ffmpeg(["-i", source, "-map", "0:a:0", "-vn", "-af",
                                      f"aresample=48000:async=1:first_pts=0,apad,atrim=duration={duration_ms / 1000}",
                                      "-ac", "2", "-c:a", "pcm_s16le", temporary], stage="mix", duration_ms=duration_ms)
                    else:
                        self._ffmpeg(["-i", aligned, "-i", source, "-filter_complex",
                                      "[1:a:0]aresample=48000:async=1:first_pts=0,volume=0.15[bg];"
                                      "[0:a][bg]amix=inputs=2:duration=first:normalize=0,alimiter=limit=0.95:latency=1[out]",
                                      "-map", "[out]", "-ac", "2", "-c:a", "pcm_s16le", temporary],
                                     stage="mix", duration_ms=duration_ms)
        return dest

    def export_video(self, source, audio, subtitles, dest, background="dub_only", burn_subtitles=False,
                     *, duration_ms=None) -> Path:
        background = {"quiet_original": "low_original", "original": "original_only"}.get(background, background)
        if background not in ("dub_only", "low_original", "original_only"):
            raise MediaError("BACKGROUND_UNAVAILABLE", "未知背景音策略。")
        source = self._source(source)
        subtitles = self._source(subtitles)
        info = self.probe(source)
        if not info["video_streams"]:
            raise MediaError("UNSUPPORTED_MEDIA", "源媒体没有视频轨。")
        if audio is None and background != "original_only":
            raise MediaError("MISSING_AUDIO", "译声导出需要完整配音，或选择保留原声。")
        if audio is not None:
            audio = self._source(audio)
        duration = min(info["duration_ms"], int(duration_ms)) if duration_ms is not None else info["duration_ms"]
        if duration <= 0:
            raise MediaError("INVALID_TIMELINE", "导出时长必须大于零。")
        if audio is not None and self._audio_duration(audio) * 1000 > duration + 30:
            raise MediaError("TTS_TOO_LONG", "译声音轨超过导出窗口，请重新对齐；不会截去配音尾部。")
        dest = Path(dest).resolve()
        with self._target(dest) as temporary:
            # A safe ASCII basename avoids Windows drive-colon/filter escaping.
            with tempfile.TemporaryDirectory(prefix="mathbridge-export-", dir=dest.parent) as folder:
                folder = Path(folder)
                safe_sub = folder / ("captions" + (subtitles.suffix or ".srt"))
                shutil.copyfile(subtitles, safe_sub)
                args = ["-i", source]
                if audio is not None:
                    args += ["-i", audio]
                if not burn_subtitles:
                    args += ["-i", safe_sub]
                args += ["-map", "0:v:0"]
                if audio is not None:
                    args += ["-map", "1:a:0"]
                elif info["audio_streams"]:
                    args += ["-map", "0:a:0"]
                if burn_subtitles:
                    args += ["-vf", f"subtitles=filename='{safe_sub.name}'", "-c:v", "libx264", "-crf", "20", "-preset", "medium"]
                else:
                    args += ["-map", f"{2 if audio is not None else 1}:s:0", "-c:s", "mov_text", "-c:v", "copy"]
                args += ["-c:a", "aac", "-b:a", "192k", "-t", f"{duration / 1000:.6f}", "-movflags", "+faststart", temporary]
                try:
                    self._ffmpeg(args, stage="export_video", duration_ms=duration, cwd=folder)
                except MediaError as exc:
                    if burn_subtitles or exc.code == "CANCELLED":
                        raise
                    position = args.index("-c:v")
                    args[position + 1] = "libx264"
                    self._ffmpeg(args, stage="export_video", duration_ms=duration, cwd=folder)
            result = self.probe(temporary)
            if not result["video_streams"] or abs(result["duration_ms"] - duration) > 300:
                raise MediaError("INVALID_OUTPUT", "导出视频轨或总时长验证失败。")
            if (audio is not None or info["audio_streams"]) and not result["audio_streams"]:
                raise MediaError("INVALID_OUTPUT", "导出文件缺少预期音轨。")
            if not burn_subtitles and not result["subtitle_streams"]:
                raise MediaError("INVALID_OUTPUT", "导出文件缺少预期字幕轨。")
        return dest

    def preview_video(self, source, audio, subtitles, dest, duration_ms=30000,
                      background="dub_only", burn_subtitles=False) -> Path:
        """Export a real first-N-seconds clip from an already bounded audio mix."""
        return self.export_video(source, audio, subtitles, dest, background=background,
                                 burn_subtitles=burn_subtitles, duration_ms=duration_ms)
