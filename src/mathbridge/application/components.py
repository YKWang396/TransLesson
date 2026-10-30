"""Locate optional components beside the installed application."""
from __future__ import annotations

from pathlib import Path
import sys


def application_directory():
    return Path(sys.executable).resolve().parent if getattr(sys,"frozen",False) else Path(__file__).resolve().parents[3]


def whisper_available(path):
    return bool(path) and all((Path(path)/name).is_file() for name in ("model.bin","config.json","tokenizer.json"))


def gpt_sovits_available(path):
    return bool(path) and all((Path(path)/name).is_file() for name in ("runtime/python.exe","api_v2.py","GPT_SoVITS/configs/tts_infer.yaml"))


def component_paths(root):
    root=Path(root)
    return dict(components_root=str(root),whisper_model_path=str(root/"faster-whisper-small"),gpt_sovits_path=str(root/"GPT-SoVITS"))


def default_components_directory():
    return application_directory()/"components"


def resolve_component_settings(values):
    result=dict(values)
    bundled=component_paths(default_components_directory())
    # Keep a usable explicitly chosen root. On a different computer, a missing
    # absolute root falls back to components beside the portable application.
    configured=result.get("components_root")
    use_bundled=not configured or not Path(configured).is_dir()
    root=bundled["components_root"] if use_bundled else configured
    paths=component_paths(root)
    result["components_root"]=root
    for key,check in (("whisper_model_path",whisper_available),("gpt_sovits_path",gpt_sovits_available)):
        if check(paths[key]) and (use_bundled or not check(result.get(key,""))):
            result[key]=paths[key]
        else:
            result.setdefault(key,"")
    return result
