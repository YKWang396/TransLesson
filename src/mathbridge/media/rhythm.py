"""Conservative PCM pause analysis. Originals and voiced samples stay intact."""
from __future__ import annotations

from array import array
import math
from pathlib import Path
import sys
import wave

from .engine import CancelledError

VERSION = "pause-rhythm-3"


def analyze_wave(path, cancel=None, *, noise_aware=False):
    levels = []
    with wave.open(str(path), "rb") as audio:
        rate, channels, width = audio.getframerate(), audio.getnchannels(), audio.getsampwidth()
        frames = audio.getnframes()
        if audio.getcomptype() != "NONE" or width not in (1, 2, 3, 4) or not frames:
            raise ValueError("INVALID_AUDIO: 节奏分析需要有效 PCM WAV")
        block = max(1, rate // 50)
        while data := audio.readframes(block):
            if cancel is not None and cancel.is_set():
                raise CancelledError()
            if width in (2, 4):
                values = array("h" if width == 2 else "i")
                values.frombytes(data)
                if sys.byteorder != "little":
                    values.byteswap()
                values = values[::max(1, rate // 8000)]
                scale = 1 if width == 2 else 65536
                level = math.sqrt(sum(v*v for v in values) / len(values)) / scale
            elif width == 1:
                level = math.sqrt(sum((v-128)**2 for v in data) / len(data))*256
            else:
                values = [int.from_bytes(data[i:i+3], "little", signed=True)/256 for i in range(0, len(data), 3)]
                level = math.sqrt(sum(v*v for v in values) / len(values))
            levels.append(level)
    duration = frames * 1000 / rate
    ordered = sorted(levels)
    # Only near silence is removed. Quiet consonants and breaths are padded.
    peak = ordered[min(len(ordered)-1, int(len(ordered)*.9))]
    threshold = max(1., min(8., peak*.002))
    if noise_aware:
        floor = ordered[int(len(ordered)*.1)]
        threshold = max(24.,min(peak*.2,max(24.,floor*2)))
    intervals = []
    for i, level in enumerate(levels):
        if level <= threshold:
            continue
        start, end = max(0., i*block*1000/rate-80), min(duration, (i+1)*block*1000/rate+80)
        if intervals and start-intervals[-1][1] < 120:
            intervals[-1][1] = end
        else:
            intervals.append([start, end])
    return dict(duration_ms=duration, sample_rate=rate, channels=channels, width=width,
                voiced=intervals, threshold=threshold, version=VERSION)


def profile_for(analysis, start, end):
    intervals = [(max(start,a),min(end,b)) for a,b in analysis["voiced"] if a < end and b > start]
    speech = sum(b-a for a,b in intervals)
    return dict(voiced_ms=round(speech), pause_ratio=round(1-speech/max(1,end-start),4),
                pause_count=max(0,len(intervals)-1))


def compact_pauses(source, destination, analysis, source_profile):
    """Copy speech regions verbatim and shorten only measured quiet intervals."""
    voiced = analysis["voiced"]
    duration = analysis["duration_ms"]
    if not voiced:
        return dict(saved_ms=0, duration_ms=duration, pause_count=0)
    keep = 180 + min(.5,max(0,source_profile.get("pause_ratio",0)))*200
    ranges = []
    position = 0.
    for start,end in voiced:
        gap = start-position
        retained = min(gap, 80 if position == 0 else keep)
        if retained > 0:
            ranges.append((start-retained,start))
        ranges.append((start,end))
        position = end
    if duration > position:
        ranges.append((position,min(duration,position+80)))
    destination = Path(destination)
    temporary = destination.with_name("."+destination.name+".tmp")
    try:
        with wave.open(str(source),"rb") as audio, wave.open(str(temporary),"wb") as output:
            output.setparams(audio.getparams())
            frames = 0
            for start,end in ranges:
                first = round(start*audio.getframerate()/1000)
                count = round(end*audio.getframerate()/1000)-first
                audio.setpos(first)
                data = audio.readframes(count)
                output.writeframesraw(data)
                frames += len(data)//(audio.getnchannels()*audio.getsampwidth())
            new_duration = frames*1000/audio.getframerate()
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)
    return dict(saved_ms=round(duration-new_duration,3), duration_ms=new_duration,
                pause_count=max(0,len(voiced)-1))
