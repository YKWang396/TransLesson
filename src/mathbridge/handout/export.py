"""Evidence-linked A4 handouts with embedded CJK text and offline formula images."""
from __future__ import annotations

import hashlib
import html
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import xml.etree.ElementTree as ET

from mathbridge.math.analysis import validate_latex

_TOKEN = re.compile(r"\{\{([a-z][a-z0-9_-]{0,63})\}\}")
_FONT = "MathBridgeCJK"


def _timestamp(ms):
    return f"{ms // 60000:02d}:{ms // 1000 % 60:02d}.{ms % 1000:03d}"


def _validate(project):
    maps = {key: {} for key in ("segments", "formulas", "glossary", "notes")}
    for key, mapping in maps.items():
        for entity in project.get(key, []):
            if entity["id"] in mapping:
                raise ValueError("Duplicate " + key + " id: " + entity["id"])
            mapping[entity["id"]] = entity
    for segment in maps["segments"].values():
        if set(segment["formula_ids"]) - maps["formulas"].keys():
            raise ValueError("Unknown segment formula reference")
        if set(segment.get("term_ids", [])) - maps["glossary"].keys():
            raise ValueError("Unknown glossary reference")
        if not 0 <= segment["start_ms"] < segment["end_ms"] <= project["source"]["duration_ms"]:
            raise ValueError("Invalid segment timeline")
        if segment.get("target_text") and set(_TOKEN.findall(segment["target_text"])) != set(segment["formula_ids"]):
            raise ValueError("Segment placeholder references disagree")
    for note in maps["notes"].values():
        if set(note["segment_ids"]) - maps["segments"].keys() or set(note["formula_ids"]) - maps["formulas"].keys():
            raise ValueError("Unknown handout source reference")
        if note["kind"] == "course" and not note["segment_ids"] and not note["formula_ids"]:
            raise ValueError("Course statement lacks evidence")
        if set(_TOKEN.findall(note["body"])) != set(note["formula_ids"]):
            raise ValueError("Handout formula placeholders disagree")
        if re.search(r"\{\{|\}\}", _TOKEN.sub("", note["body"])):
            raise ValueError("Malformed handout placeholder")
    for formula in maps["formulas"].values():
        if not formula.get("evidence"):
            raise ValueError("Formula has no source evidence")
        if formula.get("latex"):
            validate_latex(formula["latex"])
        elif formula.get("review") == "confirmed":
            raise ValueError("Confirmed formula needs valid LaTeX")
        for ev in formula["evidence"]:
            if not 0 <= ev["start_ms"] <= ev["end_ms"] <= project["source"]["duration_ms"]:
                raise ValueError("Formula evidence outside source duration")
    return maps


def _font():
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    if _FONT in pdfmetrics.getRegisteredFontNames():
        return _FONT
    # Use a tested static TTF and embed its glyphs. Do not bundle Windows fonts.
    paths = [os.environ.get("MATHBRIDGE_CJK_FONT", ""),
             "C:/Windows/Fonts/Deng.ttf", "C:/Windows/Fonts/simhei.ttf",
             "/usr/share/fonts/truetype/arphic/uming.ttf"]
    for path in paths:
        if path and Path(path).is_file():
            try:
                pdfmetrics.registerFont(TTFont(_FONT, path))
                return _FONT
            except Exception:
                continue
    raise RuntimeError("PDF_RENDER_FAILED: 未找到可嵌入的静态中文 TTF；请设置 MATHBRIDGE_CJK_FONT")


def _render_formulas(formulas, destination):
    from PySide6.QtCore import QByteArray, QRectF, Qt
    from PySide6.QtGui import QImage, QPainter
    from PySide6.QtSvg import QSvgRenderer

    selected = [f for f in formulas if f.get("latex") and f.get("review") != "preserve_source"]
    if not selected:
        return {}
    helper = Path(__file__).with_name("render_math.cjs")
    node = os.environ.get("MATHBRIDGE_NODE") or shutil.which("node")
    if not node:
        raise RuntimeError("PDF_RENDER_FAILED: 本地 Node.js 未安装，不能安全排版数学公式")
    try:
        result = subprocess.run([node, str(helper)], input=json.dumps([f["latex"] for f in selected]),
            text=True, encoding="utf-8", capture_output=True, timeout=30,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError("PDF_RENDER_FAILED: MathJax 本地排版失败或超时") from exc
    if result.returncode:
        reason = "缺少本地 MathJax 依赖" if "Cannot find module" in result.stderr else "公式语法不受支持，请校对"
        raise RuntimeError("PDF_RENDER_FAILED: " + reason)
    try:
        svgs = json.loads(result.stdout)
        if not isinstance(svgs, list) or len(svgs) != len(selected):
            raise ValueError("Bad SVG count")
    except (ValueError, TypeError) as exc:
        raise RuntimeError("PDF_RENDER_FAILED: MathJax 返回无效结构") from exc
    rendered = {}
    for formula, svg in zip(selected, svgs):
        if len(svg) > 2_000_000:
            raise ValueError("Formula SVG exceeds output bound")
        tree = ET.fromstring(svg)
        for element in tree.iter():
            if element.tag.split("}")[-1] in {"script", "foreignObject", "image"}:
                raise ValueError("Unsafe SVG output")
            for key, value in element.attrib.items():
                if key.split("}")[-1].lower().startswith("on") or re.search(r"(?:https?|file|data|javascript):", value, re.I):
                    if key != "xmlns":
                        raise ValueError("External SVG resource forbidden")
        view = [float(v) for v in tree.attrib["viewBox"].split()]
        width_pt = view[2] / 1000 * 13
        height_pt = view[3] / 1000 * 13
        if not (0 < width_pt <= 1200 and 0 < height_pt <= 650):
            raise ValueError("PDF_RENDER_FAILED: 公式过宽或过高，请分行后重新导出")
        if width_pt > 455:
            # Avoid illegible shrinking. Let the reviewer explicitly break the formula.
            raise ValueError("PDF_RENDER_FAILED: 公式宽度超过 A4 正文，请使用 aligned 受控分行")
        # Qt interprets an ex width using a different default font; set physical SVG size.
        tree.set("width", str(width_pt))
        tree.set("height", str(height_pt))
        payload = ET.tostring(tree, encoding="utf-8")
        renderer = QSvgRenderer(QByteArray(payload))
        if not renderer.isValid():
            raise ValueError("PDF_RENDER_FAILED: QtSvg 无法渲染公式")
        width_px = max(1, round(width_pt * 300 / 72))
        height_px = max(1, round(height_pt * 300 / 72))
        if width_px * height_px > 15_000_000:
            raise ValueError("Formula rasterization limit exceeded")
        image = QImage(width_px, height_px, QImage.Format.Format_ARGB32)
        image.fill(Qt.GlobalColor.transparent)
        painter = QPainter(image)
        renderer.render(painter, QRectF(0, 0, width_px, height_px))
        painter.end()
        base = destination / ("formula-" + hashlib.sha256(formula["latex"].encode()).hexdigest()[:24])
        base.with_suffix(".svg").write_bytes(payload)
        base.with_suffix(".tex").write_text(formula["latex"], encoding="utf-8")
        if not image.save(str(base.with_suffix(".png"))):
            raise RuntimeError("PDF_RENDER_FAILED: 无法保存公式图")
        rendered[formula["id"]] = (base.with_suffix(".png"), width_pt, height_pt)
    return rendered


def _source_image(project, formula, destination):
    from PIL import Image
    root = project.get("_project_dir")
    frames = [ev for ev in formula["evidence"] if ev["kind"] == "frame"]
    if not frames:
        return None
    if not root:
        raise ValueError("PDF_RENDER_FAILED: 保留原图需要项目根目录")
    root = Path(root).resolve()
    ev = frames[0]
    relative = ev["frame_path"]
    if "\\" in relative or Path(relative).is_absolute() or ".." in Path(relative).parts:
        raise ValueError("Invalid source image path")
    source = (root / relative).resolve()
    if not source.is_relative_to(root) or not source.is_file():
        raise ValueError("PDF_RENDER_FAILED: 公式原图证据已丢失")
    with Image.open(source) as image:
        x1, y1, x2, y2 = ev["bbox_xyxy"]
        if not 0 <= x1 < x2 <= 1 or not 0 <= y1 < y2 <= 1:
            raise ValueError("Invalid source crop")
        crop = image.convert("RGB").crop((int(x1 * image.width), int(y1 * image.height), int(x2 * image.width), int(y2 * image.height)))
        output = destination / ("evidence-" + hashlib.sha256(formula["id"].encode()).hexdigest()[:20] + ".png")
        crop.save(output)
        width = min(455, crop.width * 72 / 150)
        height = width * crop.height / crop.width
        if height > 600:
            raise ValueError("Source evidence image too tall; select a smaller region")
        return output, width, height


def export_handout(project: dict, output_pdf: Path, output_md: Path | None = None,
                   *, project_dir: Path | None = None) -> Path:
    """Export a revision snapshot. Unconfirmed material is visibly marked as draft.

    Source references are validated; that does not certify the mathematics. Model
    supplements are never introduced by this exporter. Existing files are replaced
    atomically, with overwrite choice delegated to the caller's export workflow.
    """
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_CENTER
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Image, KeepTogether

    if project_dir is not None:
        project = {**project, "_project_dir": str(Path(project_dir).resolve())}
    maps = _validate(project)
    formula_labels = {fid: f"公式 {index}" for index, fid in enumerate(maps["formulas"], 1)}
    output_pdf = Path(output_pdf).resolve()
    output_pdf.parent.mkdir(parents=True, exist_ok=True)
    font = _font()
    styles = {
        "title": ParagraphStyle("Title", fontName=font, fontSize=22, leading=30, textColor=colors.HexColor("#143D59"), spaceAfter=14),
        "heading": ParagraphStyle("Heading", fontName=font, fontSize=15, leading=23, spaceBefore=16, spaceAfter=9, keepWithNext=True, textColor=colors.HexColor("#143D59")),
        "body": ParagraphStyle("Body", fontName=font, fontSize=11, leading=18, wordWrap="CJK", spaceAfter=8),
        "caption": ParagraphStyle("Caption", fontName=font, fontSize=9, leading=14, wordWrap="CJK", textColor=colors.HexColor("#536570"), spaceAfter=8),
        "formula": ParagraphStyle("Formula", fontName=font, fontSize=11, leading=18, alignment=TA_CENTER, spaceAfter=8),
    }
    def paragraph(text, style="body"):
        return Paragraph(html.escape(str(text)).replace("\n", "<br/>"), styles[style])

    with tempfile.TemporaryDirectory(prefix="mathbridge-pdf-", dir=output_pdf.parent) as tmp:
        temporary = Path(tmp)
        formula_images = _render_formulas(list(maps["formulas"].values()), temporary)
        draft = any(e.get("review") != "confirmed" for key in ("segments", "formulas", "notes") for e in project.get(key, []))
        story = [paragraph(project["title"], "title"),
                 paragraph(f"课程讲义 · revision {project['revision']} · {'待校对草稿' if draft else '已校对快照'}", "caption"),
                 paragraph(f"语言：{project['languages']['source']} → {project['languages']['target']}　时长：{_timestamp(project['source']['duration_ms'])}", "caption"),
                 paragraph("课程内容均附来源索引。来源存在与公式可排版不代表数学结论已验证。", "caption")]
        markdown = [f"# {project['title']}", "", f"revision {project['revision']} · {'待校对草稿' if draft else '已校对快照'}", ""]

        def formula_flow(fid):
            formula = maps["formulas"][fid]
            parts = [paragraph(formula_labels[fid] + " · " + {"confirmed": "已校对", "needs_review": "待校对", "preserve_source": "保留原始证据"}[formula["review"]], "caption")]
            if fid in formula_images:
                path, width, height = formula_images[fid]
                parts.append(Image(str(path), width=width, height=height, hAlign="CENTER"))
                parts.append(Spacer(1, 7))
            elif formula["review"] == "preserve_source":
                parts.append(paragraph("保留原始表达（未确认公式含义）：" + formula["raw"], "body"))
            else:
                parts.append(paragraph("公式尚未识别，待校对：" + formula["raw"]))
            if formula["review"] == "preserve_source":
                source_image = _source_image(project, formula, temporary)
                if source_image:
                    path, width, height = source_image
                    parts.append(Image(str(path), width=width, height=height))
            parts.append(paragraph("来源 " + "；".join(_timestamp(e["start_ms"]) + "-" + _timestamp(e["end_ms"]) + " " + ("画面" if e["kind"] == "frame" else "语音") for e in formula["evidence"]), "caption"))
            if formula.get("warnings"):
                labels = {"RULE_CANDIDATE_REQUIRES_REVIEW": "规则生成的公式候选", "AMBIGUOUS_DENOMINATOR": "分母范围可能有歧义", "VISUAL_FORMULA_REQUIRES_REVIEW": "来自画面的公式候选"}
                warnings = list(dict.fromkeys(labels.get(code, "存在需核对的数学表达") for code in formula["warnings"]))
                status = "校对记录：" if formula["review"] != "needs_review" else "待核对："
                parts.append(paragraph(status + "；".join(warnings), "caption"))
            return KeepTogether(parts)

        if project.get("notes"):
            def markdown_formula(match):
                formula = maps["formulas"][match[1]]
                if formula["review"] == "preserve_source":
                    evidence = "；".join(_timestamp(item["start_ms"]) + "-" + _timestamp(item["end_ms"]) for item in formula["evidence"])
                    return "【原始表达，未确认：" + formula["raw"] + "；来源 " + evidence + "】"
                return "$" + (formula.get("latex") or formula["raw"]) + "$"
            for note in project["notes"]:
                label = " [补充解释，非原课内容]" if note["kind"] == "supplement" else ""
                story.append(paragraph(note["heading"] + label, "heading"))
                if note["review"] != "confirmed":
                    story.append(paragraph("本节待校对", "caption"))
                # Formula placeholders have their own display blocks, so formulas never split.
                body = _TOKEN.sub(lambda m: "【" + formula_labels[m[1]] + "，见下式】", note["body"])
                story.append(paragraph(body))
                for fid in note["formula_ids"]:
                    story.append(formula_flow(fid))
                references = [f"{sid} {_timestamp(maps['segments'][sid]['start_ms'])}-{_timestamp(maps['segments'][sid]['end_ms'])}" for sid in note["segment_ids"]]
                story.append(paragraph("课程来源：" + ("；".join(references) or "引用公式的证据时间"), "caption"))
                markdown.extend([f"## {note['heading']}{label}", "", _TOKEN.sub(markdown_formula, note["body"]), "", "来源：" + "；".join(references), ""])
        else:
            story.extend([paragraph("逐段证据索引", "heading"), paragraph("尚未生成结构化讲义。以下为课程片段索引，供整理与校对使用。", "caption")])
            markdown.extend(["## 逐段证据索引", "", "尚未生成结构化讲义。", ""])
            for segment in project.get("segments", []):
                stamp = _timestamp(segment["start_ms"]) + "-" + _timestamp(segment["end_ms"])
                text = segment.get("subtitle_plain") or segment["source_text"]
                story.extend([paragraph(stamp + " · " + segment["id"], "caption"), paragraph(text)])
                markdown.extend([stamp + " " + segment["id"], "", text, ""])
            if maps["formulas"]:
                story.append(paragraph("公式候选与证据", "heading"))
                for fid in maps["formulas"]:
                    story.append(formula_flow(fid))
                    formula = maps["formulas"][fid]
                    if formula["review"] == "preserve_source":
                        markdown.extend(["原始表达（未确认）：" + formula["raw"], "来源：" + "；".join(
                            _timestamp(item["start_ms"]) + "-" + _timestamp(item["end_ms"]) for item in formula["evidence"]), ""])
        if project.get("glossary"):
            story.append(paragraph("课程术语表", "heading"))
            markdown.extend(["## 课程术语表", ""])
            for term in project["glossary"]:
                line = f"{term['source']} → {term['target']}" + ("（锁定）" if term.get("locked") else "")
                story.append(paragraph(line))
                if term.get("context"):
                    story.append(paragraph("语境：" + term["context"], "caption"))
                markdown.extend([line, ""])
        def page_footer(canvas, doc):
            canvas.saveState()
            canvas.setStrokeColor(colors.HexColor("#DDE5EA"))
            canvas.line(23 * mm, 19 * mm, A4[0] - 23 * mm, 19 * mm)
            canvas.setFont(font, 9)
            canvas.setFillColor(colors.HexColor("#536570"))
            canvas.drawString(23 * mm, 14 * mm, f"MathBridge · revision {project['revision']}")
            canvas.drawRightString(A4[0] - 23 * mm, 14 * mm, str(doc.page))
            canvas.restoreState()
        target = temporary / "handout.pdf"
        SimpleDocTemplate(str(target), pagesize=A4, rightMargin=23 * mm, leftMargin=23 * mm,
                          topMargin=20 * mm, bottomMargin=26 * mm,
                          title=project["title"], author="MathBridge", allowSplitting=True).build(
                              story, onFirstPage=page_footer, onLaterPages=page_footer)
        # Preserve standalone glyph SVG and LaTeX for later repagination.
        assets = output_pdf.with_name(output_pdf.stem + "-formulas")
        if formula_images:
            assets.mkdir(exist_ok=True)
            for path in temporary.iterdir():
                if path.suffix in (".svg", ".tex"):
                    shutil.copy2(path, assets / path.name)
        os.replace(target, output_pdf)
        if output_md is not None:
            output_md = Path(output_md).resolve()
            output_md.parent.mkdir(parents=True, exist_ok=True)
            md_tmp = output_md.with_name(output_md.name + ".tmp")
            md_tmp.write_text("\n".join(markdown), encoding="utf-8")
            os.replace(md_tmp, output_md)
    return output_pdf
