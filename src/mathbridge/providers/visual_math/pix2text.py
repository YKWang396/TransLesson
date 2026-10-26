from __future__ import annotations

import hashlib
from pathlib import Path

from mathbridge.math.analysis import validate_latex


def _evidence(project_root: Path, region: dict) -> tuple[dict, Path]:
    root = Path(project_root).resolve()
    relative = region["frame_path"]
    if not isinstance(relative, str) or "\\" in relative or Path(relative).is_absolute() or ".." in Path(relative).parts:
        raise ValueError("Frame path must be a project-relative POSIX path")
    path = (root / relative).resolve()
    if not path.is_relative_to(root) or not path.is_file():
        raise ValueError("Source evidence image missing or outside project")
    bbox = region.get("bbox_xyxy", [0, 0, 1, 1])
    if (not isinstance(bbox, (list, tuple)) or len(bbox) != 4 or
            any(not isinstance(n, (int, float)) or not 0 <= n <= 1 for n in bbox) or
            bbox[0] >= bbox[2] or bbox[1] >= bbox[3]):
        raise ValueError("Invalid normalized frame bounding box")
    timestamp = region["start_ms"]
    if not isinstance(timestamp, int) or timestamp < 0:
        raise ValueError("Invalid frame timestamp")
    digest = hashlib.sha256(f"{relative}:{timestamp}:{bbox}".encode()).hexdigest()[:16]
    return {"id": "ev-frame-" + digest, "kind": "frame", "start_ms": timestamp,
            "end_ms": timestamp, "frame_path": relative, "bbox_xyxy": list(bbox)}, path


def manual_candidate(project_root: Path, region: dict, latex: str | None = None) -> dict:
    """Retain original frame plus normalized crop coordinates; never remove it."""
    evidence, _ = _evidence(project_root, region)
    if latex is not None:
        validate_latex(latex)
    return {"id": "formula-" + evidence["id"].removeprefix("ev-frame-"),
            "raw": latex or "尚未识别的画面公式（保留原图）", "latex": latex,
            "spoken_source": "", "spoken_target": "", "review": "needs_review",
            "warnings": ["VISUAL_FORMULA_REQUIRES_REVIEW"], "evidence": [evidence]}


def recognize(project_root: Path, frame_regions: list[dict], settings: dict,
              cancel=None, progress=None) -> list[dict]:
    """Run only an explicitly preloaded Pix2Text instance.

    Constructing Pix2Text can download weights. This adapter never constructs one;
    local-model setup must load all weights beforehand and supply `pix2text_model`.
    Manual candidates remain usable when OCR is unavailable. No image API is called.
    """
    model = settings.get("pix2text_model")
    if model is None:
        raise RuntimeError("MISSING_MODEL: 请先安装 Pix2Text 并显式加载全部本地权重；当前可手动输入 LaTeX 并保留原图")
    if settings.get("provider") != "pix2text_local" or settings.get("local_weights_verified") is not True:
        raise ValueError("Local Pix2Text weights must be explicitly verified; implicit downloads are forbidden")
    from PIL import Image
    formulas = []
    for index, region in enumerate(frame_regions):
        if cancel is not None and (cancel() if callable(cancel) else cancel.is_set()):
            raise RuntimeError("CANCELLED: 视觉识别已取消")
        evidence, path = _evidence(project_root, region)
        with Image.open(path) as image:
            x1, y1, x2, y2 = evidence["bbox_xyxy"]
            crop = image.convert("RGB").crop((int(x1 * image.width), int(y1 * image.height),
                                               int(x2 * image.width), int(y2 * image.height)))
            if crop.width < 2 or crop.height < 2:
                raise ValueError("Formula crop is too small")
            recognized = model.recognize_formula(crop)
        latex = recognized if isinstance(recognized, str) else recognized.get("text") if isinstance(recognized, dict) else None
        candidate = manual_candidate(project_root, region)
        if latex and isinstance(latex, str):
            try:
                validate_latex(latex)
                candidate.update({"raw": latex, "latex": latex})
            except ValueError:
                candidate["warnings"].append("OCR_UNSAFE_OR_UNSUPPORTED_LATEX")
        else:
            candidate["warnings"].append("OCR_UNRECOGNIZED")
        formulas.append(candidate)
        if progress:
            progress(index + 1, len(frame_regions))
    return formulas
