"""Opt-in real API QA on a separate copy of a course, with a bounded test budget."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import sys
import threading
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from mathbridge.storage.project_store import ProjectStore, atomic_json
from mathbridge.storage.credentials import load_text_secret
from mathbridge.ui.settings import load_settings
from mathbridge.worker.__main__ import execute


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=Path.home()/"Desktop"/"trans")
    parser.add_argument("--resume", type=Path)
    parser.add_argument("--test-budget", type=float, default=1.5)
    args = parser.parse_args()
    source = args.source.resolve()
    source_bytes = (source/"project.json").read_bytes()
    source_hash = hashlib.sha256(source_bytes).hexdigest()
    settings = load_settings()
    if not settings.get("remember_api_key") or not settings.get("allow_text_api"):
        raise SystemExit("Real API QA requires a saved Windows credential and course-text permission.")
    settings["api_key"] = load_text_secret(settings)
    if not settings["api_key"]:
        raise SystemExit("No saved API key available.")
    baseline = json.loads((source/"runtime.json").read_text(encoding="utf-8")).get("usage", {})
    baseline_cost = baseline.get("reserved_total_cny", baseline.get("cost_cny", 0))
    settings.update(usage_spent_cny=baseline_cost, usage_calls=baseline.get("calls", 0),
                    budget_cny=min(settings["budget_cny"], baseline_cost+args.test_budget),
                    batch_size=16, max_output_tokens=8192)
    folder = args.resume.resolve() if args.resume else ROOT/"tmp"/("course-ai-"+uuid.uuid4().hex[:10])
    folder.mkdir(parents=True, exist_ok=True)
    report_path = folder/"qa-report.json"
    report = json.loads(report_path.read_text(encoding="utf-8")) if report_path.exists() else {
        "source_project_sha256": source_hash, "completed_stages": [], "usage": [], "qa_directory": str(folder)}
    if report["source_project_sha256"] != source_hash:
        raise SystemExit("Source changed since this QA copy was created; use a new QA directory.")
    store = ProjectStore(folder)
    if not (folder/"project.json").exists():
        store.save(json.loads(source_bytes))
        for relative in ("audio/asr-raw.json", "audio/source-16k.json"):
            if (source/relative).is_file():
                (folder/relative).parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source/relative, folder/relative)
    for usage in report["usage"]:
        settings["usage_spent_cny"] += usage.get("cost_cny", 0)+usage.get("uncertain_cost_cny", 0)
        settings["usage_calls"] += usage.get("calls", 0)
    print("QA directory:", folder, flush=True)
    for stage in ("sentences", "analyze", "translate", "notes"):
        if stage in report["completed_stages"]:
            continue
        project = store.load()
        request_id = "req-qa-"+uuid.uuid4().hex
        task_settings = dict(settings, _project_dir=str(folder), _request_id=request_id)
        print("START", stage, flush=True)
        def progress(message):
            if message.get("type") == "progress":
                print(message["stage"], message["completed"], "/", message["total"], message["message"], flush=True)
        try:
            result = execute(dict(stage=stage, project=project, project_dir=str(folder), request_id=request_id,
                                  settings=task_settings, options={}, source_path=""), threading.Event(), progress)
        except BaseException:
            # Persist all paid/uncertain usage, including a failed request, so a
            # resumed QA cannot silently restart its test spending allowance.
            ledger = folder/"cache/api-reservations"/(request_id+".json")
            if ledger.exists():
                reservation = json.loads(ledger.read_text(encoding="utf-8"))
                usage = reservation["usage"]
                usage["uncertain_cost_cny"] = usage.get("uncertain_cost_cny", 0)+reservation.get("pending_cost_cny", 0)
                report["usage"].append(usage)
            atomic_json(report_path, report)
            raise
        candidate = result["project"]
        candidate["revision"] = project["revision"]+1
        store.save(candidate)
        usage = result.get("metadata", {}).get("usage", {})
        report["usage"].append(usage)
        report["completed_stages"].append(stage)
        report[stage] = {"segments": len(candidate["segments"]), "notes": len(candidate["notes"]),
                         "metadata": result.get("metadata", {}), "outputs": result.get("outputs", [])}
        atomic_json(report_path, report)
        settings["usage_spent_cny"] += usage.get("cost_cny", 0)+usage.get("uncertain_cost_cny", 0)
        settings["usage_calls"] += usage.get("calls", 0)
        print("DONE", stage, "calls", usage.get("calls", 0), "estimated CNY", round(usage.get("cost_cny", 0), 4), flush=True)
    assert hashlib.sha256((source/"project.json").read_bytes()).hexdigest() == source_hash
    print("PASS: real API course QA; original project unchanged", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
