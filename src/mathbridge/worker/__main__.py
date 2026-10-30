from __future__ import annotations

import copy
import json
import os
import sys
import threading
import time
import uuid
from pathlib import Path

from mathbridge.domain.project import file_hash, fingerprint, new_project, safe_path, segment_fingerprint, speaker_voice, validate_project, export_issues
from mathbridge.storage.project_store import atomic_json


def execute(command: dict, cancel: threading.Event, report) -> dict:
    root = Path(command["project_dir"]).resolve()
    project = copy.deepcopy(command.get("project"))
    settings = command.get("settings", {})
    options = command.get("options", {})
    stage = command["stage"]
    source = Path(command.get("source_path", ""))
    outputs, metadata, artifacts = [], {}, []

    def progress(name, completed=0, total=0, message=""):
        if cancel.is_set():
            raise InterruptedError("任务已取消，已完成片段可在重试时复用")
        report(dict(type="progress", stage=str(name), completed=int(completed or 0), total=int(total or 0), message=str(message)))

    def engine():
        from mathbridge.media.engine import MediaEngine
        return MediaEngine(settings.get("ffmpeg_path") or None, settings.get("ffprobe_path") or None, cancel, progress)

    def source_audio():
        wav = safe_path(root, "audio/source-16k.wav")
        marker = wav.with_suffix(".json")
        valid = False
        try:
            cached = json.loads(marker.read_text(encoding="utf-8"))
            valid = isinstance(cached, dict) and wav.is_file() and cached.get("source_sha256") == project["source"]["sha256"] and cached.get("sha256") == file_hash(wav, cancel)
        except (OSError, ValueError):
            pass
        if not valid:
            engine().extract_audio(source, wav)
            atomic_json(marker, dict(source_sha256=project["source"]["sha256"], sha256=file_hash(wav, cancel)))
        return wav

    if stage == "create":
        if (root / "project.json").exists():
            raise ValueError("PROJECT_EXISTS: 请选择新项目文件夹")
        if not source.is_file():
            raise ValueError("SOURCE_MISSING: 视频文件不存在")
        progress("probe", 0, 0, "正在探测媒体与计算源文件指纹")
        info = engine().probe(source)
        if not info.get("video_streams"):
            raise ValueError("UNSUPPORTED_MEDIA: 请选择包含画面的视频课程文件")
        source_hash = file_hash(source, cancel)
        project = new_project(options.get("title") or source.stem,
                              dict(path="inputs/source.link.json", sha256=source_hash, duration_ms=info["duration_ms"],
                                   timeline_origin_ms=info.get("timeline_origin_ms", 0)),
                              options.get("source_language", "en"), options.get("target_language", "zh-CN"))
        stat = source.stat()
        metadata = dict(media=info, source_link=dict(absolute_path=str(source.resolve()), sha256=source_hash,
                                                    size=stat.st_size, mtime_ns=stat.st_mtime_ns))
        validate_project(project)
        return dict(project=project, outputs=[], metadata=metadata)

    if not project:
        raise ValueError("请先打开项目")
    validate_project(project)
    if stage in {"transcribe", "diarize", "speaker_reference", "export", "dub", "frame", "preview", "relink"}:
        if not source.is_file():
            if stage == "export" and not set(options.get("formats", [])) & {"video", "wav"}:
                pass
            else:
                raise ValueError("SOURCE_MISSING: 原视频已移动，请重新定位")
        elif file_hash(source, cancel) != project["source"]["sha256"]:
            raise ValueError("SOURCE_CHANGED: 原文件内容已改变，不能复用旧项目输入")

    job_id = command["request_id"]
    job_dir = safe_path(root, f"cache/jobs/{job_id}")
    job_dir.mkdir(parents=True, exist_ok=True)

    if stage == "relink":
        stat = source.stat()
        return dict(outputs=[], metadata=dict(source_link=dict(absolute_path=str(source.resolve()), sha256=project["source"]["sha256"], size=stat.st_size, mtime_ns=stat.st_mtime_ns)))

    if stage == "import_subtitles":
        from mathbridge.application.subtitles import import_source_subtitles
        if project["segments"] and not options.get("replace_existing"):
            raise ValueError("TRANSCRIPT_EXISTS: 为保护已有校对，请在新项目导入字幕")
        project["segments"] = import_source_subtitles(Path(options["path"]), project["source"]["duration_ms"])
        project["formulas"], project["glossary"], project["notes"] = [], [], []

    elif stage == "transcribe":
        if project["segments"] and not options.get("replace_existing"):
            raise ValueError("TRANSCRIPT_EXISTS: 已存在转写，请新建项目或显式允许替换")
        model = settings.get("whisper_model_path", "")
        if not model or not Path(model).is_dir():
            raise ValueError("MISSING_MODEL: 请设置已下载的 faster-whisper 多语言模型文件夹；不会自动联网下载")
        from mathbridge.providers.asr.whisper import transcribe
        wav = source_audio()
        progress("transcribe", 0, 0, "本地语音识别运行中")
        import inspect
        params = inspect.signature(transcribe).parameters
        kwargs = dict(cancel=cancel, progress=progress)
        if "raw_output" in params:
            kwargs["raw_output"] = safe_path(root, "audio/asr-raw.json")
        for key in ("device", "compute_type"):
            if key in params:
                kwargs[key] = settings.get("whisper_" + key, "cpu" if key == "device" else "int8")
        project["segments"] = transcribe(wav, project["languages"]["source"], model, **kwargs)
        if not project["segments"]:
            raise ValueError("EMPTY_TRANSCRIPT: 未识别到可用语音，请检查音轨或导入已有字幕")
        for segment in project["segments"]:
            segment["end_ms"] = min(segment["end_ms"], project["source"]["duration_ms"])
        project["formulas"], project["glossary"], project["notes"] = [], [], []
        # Transcription must be available as soon as speech recognition ends.
        # A full video scan (especially AV1) is a separate, optional operation.
        metadata["frames"] = engine().sample_frames(source, job_dir / "frames", project["source"]["duration_ms"]) if options.get("sample_frames") else []

    elif stage == "diarize":
        if not project["segments"]:
            raise ValueError("EMPTY_TRANSCRIPT: 请先转写或导入字幕，再区分说话人")
        from mathbridge.providers.diarization import diarize, apply_diarization
        segmentation = settings.get("diarization_segmentation_path", "")
        embedding = settings.get("diarization_embedding_path", "")
        if not segmentation or not embedding:
            raise ValueError("MISSING_MODEL: 请在处理设置中选择本地说话人分段和声纹 ONNX 模型")
        wav = source_audio()
        turns = diarize(wav, segmentation, embedding, num_speakers=settings.get("diarization_num_speakers", 0),
                        cancel=cancel, progress=progress)
        raw_records = None
        raw_path = safe_path(root, "audio/asr-raw.json")
        if raw_path.is_file():
            raw = json.loads(raw_path.read_text(encoding="utf-8"))
            if raw.get("inputs", {}).get("audio_sha256") == file_hash(wav, cancel):
                raw_records = raw.get("segments", [])
        project, warnings = apply_diarization(project, turns, raw_records=raw_records)
        atomic_json(job_dir / "speaker-turns.json", dict(source_sha256=project["source"]["sha256"], turns=turns,
                    segmentation_sha256=file_hash(Path(segmentation), cancel), embedding_sha256=file_hash(Path(embedding), cancel), warnings=warnings))
        outputs = [str(job_dir / "speaker-turns.json")]
        metadata.update(speaker_turns=turns, warnings=warnings, speaker_count=len({t["speaker_id"] for t in turns}))

    elif stage == "speaker_reference":
        from mathbridge.application.voices import prepare_reference
        speaker = next((s for s in project["speakers"] if s["id"] == options.get("speaker_id")), None)
        if speaker is None:
            raise ValueError("SPEAKER_MISSING: 请先选择说话人")
        reference = prepare_reference(project, speaker, root, source_audio(), cancel)
        outputs = [str(reference)]
        metadata.update(speaker_id=speaker["id"], reference=True)

    elif stage == "sentences":
        if settings.get("text_provider", "manual") == "manual":
            raise ValueError("PROVIDER_UNAVAILABLE: AI 断句需要在处理设置中配置文本 API")
        if not project["segments"]:
            raise ValueError("EMPTY_TRANSCRIPT: 请先识别原文或导入字幕")
        from mathbridge.providers.translation.course import organize_sentences
        data = organize_sentences(project, settings, cancel=cancel, progress=progress)
        for key in ("segments", "formulas", "glossary", "notes"):
            project[key] = data[key]
        metadata.update(usage=data["usage"], sentences=data["sentence_metadata"])

    elif stage == "analyze":
        from mathbridge.math.analysis import analyze
        data = analyze(project)
        for key in ("segments", "formulas", "glossary", "notes"):
            if key in data:
                project[key] = data[key]
        metadata["analysis"] = {k: v for k, v in data.items() if k not in {"segments", "formulas", "glossary", "notes"}}

    elif stage == "translate":
        if settings.get("text_provider", "deepseek") == "manual":
            raise ValueError("PROVIDER_UNAVAILABLE: 当前选择手动翻译，可在校对页填写译文，或配置 API")
        if not project["segments"]:
            raise ValueError("EMPTY_TRANSCRIPT: 请先转写或导入字幕")
        from mathbridge.providers.translation.deepseek import translate
        translation_settings = dict(settings, _translation_missing_only=bool(options.get("missing_only", True)))
        data = translate(project, translation_settings, cancel=cancel, progress=progress)
        for key in ("segments", "formulas", "glossary", "notes"):
            if key in data:
                project[key] = data[key]
        metadata["usage"] = data.get("usage", {})

    elif stage in {"dub", "preview"}:
        from mathbridge.application.alignment import alignment_options, cached_alignment_report
        alignment_config = alignment_options(options)
        rhythm_audio = None
        if alignment_config["alignment_mode"] == "adaptive" and not options.get("sample_only"):
            progress("dub",0,0,"分析原声节奏与停顿")
            rhythm_audio = source_audio()
        requested = options.get("segment_ids")
        segments = [s for s in project["segments"] if not requested or s["id"] in requested]
        if stage == "preview" and not requested:
            segments = [s for s in segments if s["end_ms"] <= 30000]
        preview_duration = min(project["source"]["duration_ms"], 30000) if stage == "preview" else project["source"]["duration_ms"]
        if stage == "preview" and any(s["end_ms"] > preview_duration for s in segments):
            raise ValueError("PREVIEW_WINDOW: 请选取前30秒内完整的语义段，不能截断配音")
        if not segments:
            raise ValueError("EMPTY_TRANSCRIPT: 没有可配音语句")
        if options.get("sample_only") and len(segments) != 1:
            raise ValueError("VOICE_SAMPLE_INVALID: 请一次选择一个片段试听")
        references = {}
        for index, segment in enumerate(segments):
            progress("dub", index, len(segments), f"合成 {segment['id']}")
            if not segment["tts_text"].strip():
                raise ValueError(f"MISSING_TRANSLATION: {segment['id']} 目标语言读法为空")
            speaker = next(s for s in project["speakers"] if s["id"] == segment["speaker_id"])
            voice = speaker_voice(speaker, settings)
            fp = segment_fingerprint(project, segment, settings)
            relative = f"audio/tts/{fp}.wav"
            dest = safe_path(root, relative)
            marker = dest.with_suffix(".json")
            cache_valid = False
            if dest.exists() and marker.exists():
                try:
                    cached = json.loads(marker.read_text(encoding="utf-8"))
                    cache_valid = isinstance(cached, dict) and cached.get("sha256") == file_hash(dest, cancel) and cached.get("input_fingerprint") == fp
                except (OSError, ValueError):
                    pass
            if not cache_valid:
                if options.get("timing_sample"):
                    raise ValueError("STALE_AUDIO: 请先生成这句自然配音，再试听调整结果")
                dest.parent.mkdir(parents=True, exist_ok=True)
                if voice["provider"] == "gpt_sovits":
                    from mathbridge.application.voices import prepare_reference, prepare_auxiliary_references
                    from mathbridge.providers.tts.gpt_sovits import synthesize
                    if speaker["id"] not in references:
                        reference_args=dict(source_media=source,media_engine=engine())
                        references[speaker["id"]] = (prepare_reference(project,speaker,root,source_audio(),cancel,**reference_args),
                            prepare_auxiliary_references(project,speaker,root,source_audio(),cancel,**reference_args))
                    reference, auxiliary = references[speaker["id"]]
                    synthesize(segment["tts_text"], project["languages"]["target"], reference,
                               voice["reference_text"], voice["reference_language"], dest, endpoint=voice["endpoint"],
                               speed=voice["speed"], cancel=cancel,auxiliary_reference_audio=auxiliary)
                else:
                    from mathbridge.providers.tts.windows import synthesize
                    synthesize(segment["tts_text"], project["languages"]["target"], voice["voice"], dest,
                               cancel=cancel, rate=voice["rate"])
                atomic_json(marker, dict(sha256=file_hash(dest, cancel), input_fingerprint=fp, input_revision=project["revision"]))
            cached_metadata = json.loads(marker.read_text(encoding="utf-8"))
            artifact = dict(id="tts-" + fp[:24], kind="tts", path=relative, sha256=file_hash(dest, cancel),
                            input_fingerprint=fp, input_revision=cached_metadata.get("input_revision", project["revision"]), segment_ids=[segment["id"]], depends_on=[])
            artifacts.append(artifact)
            outputs.append(str(dest))
        if options.get("sample_only"):
            if options.get("timing_sample"):
                from mathbridge.application.dubbing import prepare_timing
                s=segments[0]
                clip=dict(segment_id=s["id"],speaker_id=s["speaker_id"],path=str(dest),start_ms=s["start_ms"],end_ms=s["end_ms"])
                prepared=prepare_timing(project,settings,root,[clip],source_audio(),cancel,progress)[0] if alignment_config["alignment_mode"]=="adaptive" else clip
                timing=cached_alignment_report(project,settings,root,alignment_config)
                plan=next(c for c in timing["plans"] if c["segment_id"]==s["id"])
                speed=min(alignment_config["alignment_max_speed"],plan["speed"])
                sample=job_dir/"matched-sample.wav"
                engine()._ffmpeg(["-i",prepared["path"],"-af",f"atempo={speed:.9f}","-c:a","pcm_s16le",sample],stage="dub")
                return dict(artifacts=artifacts,outputs=[str(sample)],metadata=dict(sample=True,timing_sample=True,speaker_id=s["speaker_id"]))
            return dict(artifacts=artifacts, outputs=outputs, metadata=dict(speaker_id=segments[0]["speaker_id"], sample=True))
        all_segments = sorted(project["segments"], key=lambda item: item["start_ms"])
        boundaries = {s["id"]: dict(index=i + 1, limit_start_ms=all_segments[i-1]["end_ms"] if i else 0,
                      limit_end_ms=all_segments[i + 1]["start_ms"]
                      if i + 1 < len(all_segments) else project["source"]["duration_ms"])
                      for i, s in enumerate(all_segments)}
        clips = [dict(path=str(safe_path(root, a["path"])), start_ms=s["start_ms"], end_ms=s["end_ms"],
                      segment_id=s["id"],speaker_id=s["speaker_id"], **boundaries[s["id"]])
                 for s, a in zip(segments, artifacts)]
        if rhythm_audio:
            from mathbridge.application.dubbing import prepare_timing
            clips = prepare_timing(project,settings,root,clips,rhythm_audio,cancel,progress)
        alignment_report = cached_alignment_report(project, settings, root, alignment_config)
        report_path = safe_path(root, "audio/alignment-issues.json" if stage == "dub" else "audio/preview-alignment-issues.json")
        atomic_json(report_path, alignment_report)
        mixed = job_dir / "dub.wav"
        try:
            engine().align_and_mix(clips, mixed, preview_duration, source=source,
                                   background=options.get("background", "dub_only"), **alignment_config)
        except Exception as exc:
            if getattr(exc, "issues", None):
                alignment_report["issues"] = exc.issues
                atomic_json(report_path, alignment_report)
            raise
        mixed_artifact = dict(id="mix-" + job_id[-20:], kind="mix", path=mixed.relative_to(root).as_posix(), sha256=file_hash(mixed, cancel),
                              input_fingerprint=fingerprint({"segments": artifacts, "background": options.get("background", "dub_only"),
                                                             "duration": project["source"]["duration_ms"], "alignment": alignment_config}),
                              input_revision=project["revision"], segment_ids=[s["id"] for s in segments],
                              depends_on=[dict(artifact_id=a["id"], sha256=a["sha256"]) for a in artifacts])
        artifacts.append(mixed_artifact)
        outputs.insert(0, str(mixed))
        metadata.update(preview_revision=project["revision"], background=options.get("background", "dub_only"),
                        alignment=alignment_report)
        if stage == "preview":
            from mathbridge.application.subtitles import write_subtitles
            preview_project = copy.deepcopy(project)
            preview_project["segments"] = segments
            from mathbridge.application.subtitles import dubbing_timing
            from mathbridge.application.alignment import plan_alignment
            preview_plans,_ = plan_alignment(clips,preview_duration,alignment_config)
            _, preview_subtitles = write_subtitles(preview_project, job_dir,dubbing_timing(preview_plans,preview_duration))
            short_video = job_dir / "preview.mp4"
            engine().preview_video(source, mixed, preview_subtitles["srt"], short_video, duration_ms=preview_duration,
                                   background=options.get("background", "dub_only"))
            outputs.append(str(short_video))
            metadata.update(preview_duration_ms=preview_duration, draft=True)

    elif stage == "frame":
        time_ms = int(options.get("time_ms", 0))
        if not 0 <= time_ms < project["source"]["duration_ms"]:
            raise ValueError("TIMELINE_INVALID: 当前时间不在视频中")
        frame = engine().frame_at(source, job_dir / f"frame-{time_ms}.png", time_ms)
        outputs = [str(frame)]
        metadata["frames"] = [dict(path=str(frame), start_ms=time_ms, end_ms=time_ms, bbox_xyxy=[0, 0, 1, 1])]

    elif stage == "notes":
        from mathbridge.handout.export import export_handout
        if settings.get("text_provider", "manual") == "manual":
            raise ValueError("PROVIDER_UNAVAILABLE: AI 教学讲义需要在处理设置中配置文本 API；已有讲义可直接导出")
        from mathbridge.providers.translation.course import generate_handout
        data = generate_handout(project, settings, cancel=cancel, progress=progress)
        project["notes"] = data["notes"]
        metadata["usage"] = data["usage"]
        if not project["notes"]:
            raise ValueError("EMPTY_NOTES: 请在处理设置中配置文本 API，AI 会按知识点整理教学讲义")
        dest = job_dir / "handout-draft.pdf"
        project["revision"] += 1
        export_handout(project, dest, job_dir / "handout-draft.md", project_dir=root)
        outputs = [str(dest), str(job_dir / "handout-draft.md")]
        metadata["draft"] = True

    elif stage == "export":
        from mathbridge.application.subtitles import write_subtitles
        from mathbridge.application.artifacts import validate_render_plan
        from mathbridge.handout.export import export_handout
        formats = options.get("formats", ["srt", "vtt", "pdf", "video"])
        allowed = {"srt", "vtt", "pdf", "video", "wav", "ass", "markdown", "json"}
        if not formats or not set(formats).issubset(allowed):
            raise ValueError("INVALID_FORMAT: 请选择有效的导出格式")
        issues = export_issues(project, formats)
        errors = [i["message"] for i in issues if i["severity"] == "error"]
        if errors:
            raise ValueError("EXPORT_BLOCKED: " + "；".join(errors[:12]))
        directory = safe_path(root, f"exports/revision-{project['revision']}-{job_id[-12:]}")
        directory.mkdir(parents=True, exist_ok=False)
        timing = None
        export_ids = []
        def add_artifact(path, kind, dependencies=(), fp=None):
            item = dict(id=kind + "-" + uuid.uuid4().hex[:16], kind=kind, path=path.relative_to(root).as_posix(),
                        sha256=file_hash(path, cancel), input_fingerprint=fp or fingerprint({"project": project, "kind": kind, "options": options}),
                        input_revision=project["revision"], segment_ids=[s["id"] for s in project["segments"]], depends_on=list(dependencies))
            artifacts.append(item)
            export_ids.append(item["id"])
            outputs.append(str(path))
            return item
        audio = None
        mix_artifact = None
        background = options.get("background", "dub_only")
        if set(formats) & {"video", "wav"} and background != "original":
            from mathbridge.application.alignment import alignment_options, cached_alignment_report
            alignment_config = alignment_options(options)
            registry = command.get("artifacts", [])
            clips = []
            for segment in project["segments"]:
                fp = segment_fingerprint(project, segment, settings)
                cached = next((a for a in reversed(registry) if a["kind"] == "tts" and a["input_fingerprint"] == fp and a["segment_ids"] == [segment["id"]]), None)
                if not cached:
                    raise ValueError(f"STALE_AUDIO: {segment['id']} 配音未生成或已过期，请先重新配音")
                path = safe_path(root, cached["path"])
                if not path.exists() or file_hash(path, cancel) != cached["sha256"]:
                    raise ValueError(f"ARTIFACT_CHANGED: {segment['id']} 配音文件已改变")
                artifacts.append(cached)
                clips.append(dict(path=str(path), start_ms=segment["start_ms"], end_ms=segment["end_ms"],
                                  segment_id=segment["id"],speaker_id=segment["speaker_id"], index=len(clips) + 1))
            audio = directory / "lesson.wav"
            if alignment_config["alignment_mode"] == "adaptive":
                from mathbridge.application.dubbing import prepare_timing
                clips = prepare_timing(project,settings,root,clips,source_audio(),cancel,progress)
            atomic_json(safe_path(root, "audio/alignment-issues.json"),
                        cached_alignment_report(project, settings, root, alignment_config))
            engine().align_and_mix(clips, audio, project["source"]["duration_ms"], source=source,
                                   background=background, **alignment_config)
            from mathbridge.application.subtitles import dubbing_timing
            from mathbridge.application.alignment import plan_alignment
            plans,_ = plan_alignment(clips,project["source"]["duration_ms"],alignment_config)
            timing = dubbing_timing(plans,project["source"]["duration_ms"])
            mix_artifact = add_artifact(audio, "mix", [dict(artifact_id=a["id"], sha256=a["sha256"]) for a in artifacts if a["kind"] == "tts"])
            if "wav" not in formats:
                outputs.remove(str(audio))
                export_ids.remove(mix_artifact["id"])
        elif "wav" in formats:
            raise ValueError("INVALID_BACKGROUND: 原声字幕模式不生成目标语言 WAV")
        cues, subtitles = write_subtitles(project,directory,timing)
        for ext in ("srt","vtt","ass"):
            if ext in formats:
                add_artifact(subtitles[ext],"subtitles")
        if set(formats) & {"pdf", "markdown"}:
            pdf = directory / "handout.pdf"
            md = directory / "handout.md"
            export_handout(project, pdf, md, project_dir=root)
            if "pdf" in formats:
                add_artifact(pdf, "pdf")
            if "markdown" in formats:
                outputs.append(str(md))
        if "video" in formats:
            video = directory / "lesson.mp4"
            sub_artifact = next((a for a in artifacts if a["kind"] == "subtitles" and a["path"].endswith(".srt")), None)
            if sub_artifact is None:
                sub_artifact = add_artifact(subtitles["srt"], "subtitles")
            engine().export_video(source, audio, subtitles["srt"], video, background=background, burn_subtitles=bool(options.get("burn_subtitles", False)))
            deps = [dict(artifact_id=sub_artifact["id"], sha256=sub_artifact["sha256"])]
            if mix_artifact:
                deps.append(dict(artifact_id=mix_artifact["id"], sha256=mix_artifact["sha256"]))
            add_artifact(video, "video", deps)
        if "json" in formats:
            atomic_json(directory / "project.json", project)
            outputs.append(str(directory / "project.json"))
        plan = dict(schema_version="1.0", illustrative=False, project_id=project["project_id"], project_revision=project["revision"],
                    source_sha256=project["source"]["sha256"], cues=cues, artifacts=artifacts, export_artifact_ids=export_ids)
        if timing is not None:
            plan["dubbing_timing"] = timing
        validate_render_plan(plan, project, root)
        atomic_json(directory / "render-plan.json", plan)
        from mathbridge import __version__
        atomic_json(directory / "export-report.json", dict(project_revision=project["revision"], background=background, warnings=issues,
                                                         created_at=time.strftime("%Y-%m-%dT%H:%M:%S%z"), software="课译TransLesson " + __version__, checks="hashes, dependency graph, cue ranges, media probe"))
        outputs.extend([str(directory / "render-plan.json"), str(directory / "export-report.json")])
        metadata.update(export_revision=project["revision"], export_directory=str(directory), warnings=issues)
    else:
        raise ValueError("UNKNOWN_STAGE: " + stage)

    if stage in {"transcribe", "import_subtitles", "diarize", "sentences", "analyze", "translate", "notes"}:
        validate_project(project)
        return dict(project=project, artifacts=artifacts, outputs=outputs, metadata=metadata)
    return dict(artifacts=artifacts, outputs=outputs, metadata=metadata)


def main():
    # Frozen Python may ignore PYTHONIOENCODING; the wire format is always UTF-8.
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="strict", newline="\n")
    # Keep native/model stdout off the protocol channel, including lazy imports.
    protocol = sys.stdout
    sys.stdout = sys.stderr
    command = json.loads(sys.stdin.readline())
    request_id = command["request_id"]
    cancel = threading.Event()
    def listen():
        for line in sys.stdin:
            try:
                if json.loads(line).get("type") == "cancel":
                    cancel.set()
            except (ValueError, TypeError):
                pass
    def report(payload):
        payload["request_id"] = request_id
        protocol.write(json.dumps(payload, ensure_ascii=False) + "\n")
        protocol.flush()
    try:
        # Windows native numerical runtimes can consult CRT file descriptors
        # while loading. Load them before a background thread blocks on stdin.
        if command.get("stage") in {"transcribe", "diarize"}:
            report(dict(type="progress", stage=command["stage"], completed=0, total=0,
                        message="正在加载本地语音识别组件"))
            import numpy
            if command["stage"] == "transcribe":
                import faster_whisper
            else:
                import sherpa_onnx
        threading.Thread(target=listen, daemon=True).start()
        payload = execute(command, cancel, report)
        if cancel.is_set():
            raise InterruptedError("任务已取消")
        report(dict(type="done", status="succeeded", input_revision=command.get("project_revision", 0), result=payload))
    except Exception as exc:
        cancelled = cancel.is_set() or isinstance(exc, InterruptedError) or "CANCEL" in str(exc).upper()
        message = str(exc)
        for key in ("api_key", "elevenlabs_api_key"):
            secret = command.get("settings", {}).get(key)
            if secret:
                message = message.replace(secret, "[redacted]")
        report(dict(type="done", status="cancelled" if cancelled else "failed", input_revision=command.get("project_revision", 0),
                    code=getattr(exc, "code", type(exc).__name__), message=message[:3000], usage=getattr(exc, "usage", {}),
                    issues=getattr(exc, "issues", [])))
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
