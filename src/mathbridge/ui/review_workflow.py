"""Choose a concrete next action from saved lesson state; never starts work."""
from __future__ import annotations

from pathlib import Path
from urllib.parse import urlsplit

from mathbridge.application.issues import obsolete_rule_formula_ids
from mathbridge.providers.translation.providers import REMOTE_PROVIDERS


def review_guidance(project, settings, *, busy=False, dirty=False):
    segments = (project or {}).get("segments", [])
    confirmed = sum(segment.get("review") == "confirmed" for segment in segments)
    formulas = sum(formula.get("review") == "needs_review" for formula in (project or {}).get("formulas", []))
    result = dict(step=0, title="先导入课程视频", description="在导入页选择视频并建立项目，然后回到这里。",
                  action="import_video", label="去导入视频", enabled=True,
                  progress=f"共 {len(segments)} 句 · 已确认 {confirmed} 句（可选）", total=len(segments),
                  confirmed=confirmed, pending_formulas=formulas)

    def step(number, title, description, action, label):
        result.update(step=number, title=title, description=description, action=action, label=label)

    if project and not segments:
        path = Path(settings.get("whisper_model_path") or "")
        ready = bool(settings.get("whisper_model_path")) and all((path / name).is_file() for name in ("model.bin", "config.json", "tokenizer.json"))
        if ready:
            step(1, "先把讲话变成原文", "模型已准备好。点击生成原文；如果已有字幕，也可以直接导入。", "transcribe", "生成课程原文")
        else:
            step(1, "先准备课程原文", "自动识别需要选择完整的 Whisper 模型。已有 SRT / VTT 字幕时，可以直接导入。", "configure_asr", "选择识别模型")
    elif segments:
        missing_count = sum(any(not str(segment.get(key, "")).strip() for key in ("target_text", "subtitle_plain", "tts_text")) for segment in segments)
        missing = missing_count > 0
        obsolete_count = len(obsolete_rule_formula_ids(project))
        provider = settings.get("text_provider", "manual")
        if obsolete_count:
            step(3, "修复旧版误识别公式", f"发现 {obsolete_count} 项旧版规则将单词末尾误认作公式。清除后只需重新翻译受影响的句子，其余译文会保留。", "repair_formulas", f"清除 {obsolete_count} 项误识别")
        elif missing and provider == "manual":
            step(2, "开启 AI 断句与翻译", "在处理设置中选择文本 API 并填写密钥，AI 会整理断句并翻译，无需逐句填写。", "configure_translation", "设置 AI 处理")
        elif missing:
            if provider == "offline":
                try:
                    parts = urlsplit(settings.get("api_base", ""))
                    ready = parts.scheme == "http" and parts.hostname == "127.0.0.1" and bool(settings.get("model"))
                except ValueError:
                    ready = False
            else:
                ready = provider in REMOTE_PROVIDERS and bool(settings.get("api_key")) and bool(settings.get("allow_text_api")) and bool(settings.get("model"))
            if ready:
                partial = missing_count < len(segments)
                organized = all(s["id"].startswith("sentence-") for s in segments)
                resume = partial or organized
                step(2, "补齐缺失译文" if resume else "AI 断句并翻译", f"仅补齐 {missing_count} 句译文，其余保留。需要优化全课断句时，点击“AI 重新断句并翻译”。" if resume else "AI 先合并碎句、合理拆分长句，再生成字幕与配音读法。无需逐句填写。", "translate_missing" if resume else "prepare_translation", f"补齐 {missing_count} 句译文" if resume else "AI 断句并翻译")
            else:
                step(2, "设置 AI 处理", "配置文本 API、密钥和课程文本许可后，即可自动断句与翻译。", "configure_translation", "设置 AI 处理")
        else:
            pending = f"另有 {formulas} 个公式需在问题列表中检查或保留原始证据。" if formulas else ""
            step(4, "字幕译文已就绪", "可以直接生成配音，逐句确认属于可选校对。优先处理问题列表中的缺失内容和配音时间问题。" + pending, "go_dub", "下一步：去配音")
    if busy:
        result.update(enabled=False, label="正在处理，请稍候", description="当前任务完成后，这里会显示下一步。你可以在底部查看进度或停止任务。")
    elif dirty:
        result.update(enabled=False, label="正在保存修改", description="本句保存后即可继续；保存失败时请检查页面中的提示。")
    return result
