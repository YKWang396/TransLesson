"""Keep a simple subtitle edit consistent with protected math and spoken text."""
from __future__ import annotations

import re

from mathbridge.domain.project import PLACEHOLDER

_SUP = str.maketrans("0123456789+-=()n", "⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻⁼⁽⁾ⁿ")


def _surfaces(formula, segment):
    candidates = set()
    latex = formula.get("latex") or ""
    if latex:
        candidates.add(latex)
        plain = latex.replace("\\left", "").replace("\\right", "")
        for macro, symbol in (("\\cdot", "·"), ("\\times", "×"), ("\\alpha", "α"), ("\\beta", "β"),
                              ("\\gamma", "γ"), ("\\theta", "θ"), ("\\pi", "π"), ("\\infty", "∞")):
            plain = plain.replace(macro, symbol)
        plain = re.sub(r"\^\{([0-9+\-=()n]+)\}|\^([0-9n])", lambda match: (match[1] or match[2]).translate(_SUP), plain)
        candidates.add(plain)
    for field in ("spoken_target",):
        if formula.get(field, "").strip():
            candidates.add(formula[field])
    # One placeholder can be recovered exactly when its surrounding wording
    # matches. With multiple placeholders, only explicit known surfaces apply.
    matches = list(PLACEHOLDER.finditer(segment.get("target_text", "")))
    if len(matches) == 1 and matches[0][1] == formula["id"]:
        match = matches[0]
        prefix, suffix = segment["target_text"][:match.start()], segment["target_text"][match.end():]
        old_plain = segment.get("subtitle_plain", "")
        if old_plain.startswith(prefix) and old_plain.endswith(suffix):
            stop = len(old_plain) - len(suffix) if suffix else len(old_plain)
            surface = old_plain[len(prefix):stop]
            if surface.strip():
                candidates.add(surface)
    return {candidate.strip() for candidate in candidates if candidate.strip()}


def simple_subtitle_changes(project, segment, subtitle):
    """Return all three derived fields, or leave ambiguous formula edits unsaved.

    Formula readings come from the reviewed formula entity. We never silently
    append a placeholder, remove a formula, or send display superscripts to TTS.
    """
    if not isinstance(subtitle, str) or "{{" in subtitle or "}}" in subtitle:
        raise ValueError("目标字幕请填写可直接阅读的文字，公式标记在高级工具中编辑")
    if not segment["formula_ids"]:
        return dict(target_text=subtitle, subtitle_plain=subtitle, tts_text=subtitle, review="needs_review")
    formulas = {formula["id"]: formula for formula in project["formulas"] if formula["id"] in segment["formula_ids"]}
    expressions = []
    normalized_owners = {}
    for identifier, formula in formulas.items():
        if not formula.get("spoken_target", "").strip():
            raise ValueError("FORMULA_REVIEW_REQUIRED: 本句公式还没有配音读法，请展开高级工具检查公式")
        for surface in _surfaces(formula, segment):
            normalized = re.sub(r"\s+", "", surface)
            if normalized in normalized_owners and normalized_owners[normalized] != identifier:
                raise ValueError("FORMULA_REVIEW_REQUIRED: 相同显示对应多个公式，请展开高级工具分别检查")
            normalized_owners[normalized] = identifier
            expressions.append((surface, identifier))
    # Long expressions win over a shorter symbol sharing the same prefix.
    expressions.sort(key=lambda item: len(re.sub(r"\s+", "", item[0])), reverse=True)
    patterns, owners = [], {}
    for index, (surface, identifier) in enumerate(expressions):
        compact = re.sub(r"\s+", "", surface)
        if not compact:
            continue
        group = f"surface{index}"
        pattern = r"\s*".join(re.escape(character) for character in compact)
        # Latin variables must not be matched inside English words.
        if compact[0].isascii() and compact[0].isalnum():
            pattern = r"(?<![A-Za-z0-9_])" + pattern
        if compact[-1].isascii() and compact[-1].isalnum():
            pattern += r"(?![A-Za-z0-9_])"
        patterns.append(f"(?P<{group}>{pattern})")
        owners[group] = identifier
    if not patterns:
        raise ValueError("FORMULA_REVIEW_REQUIRED: 本句公式需要在高级工具中检查")
    pattern = re.compile("|".join(patterns))
    found = set()
    target, spoken, position = [], [], 0
    for match in pattern.finditer(subtitle):
        identifier = owners[match.lastgroup]
        found.add(identifier)
        target += [subtitle[position:match.start()], "{{" + identifier + "}}"]
        spoken += [subtitle[position:match.start()], formulas[identifier]["spoken_target"]]
        position = match.end()
    if found != set(segment["formula_ids"]):
        raise ValueError("FORMULA_REVIEW_REQUIRED: 字幕中的公式已改变或无法自动对应，请展开高级工具检查公式和读法")
    target.append(subtitle[position:])
    spoken.append(subtitle[position:])
    return dict(target_text="".join(target), subtitle_plain=subtitle, tts_text="".join(spoken), review="needs_review")
