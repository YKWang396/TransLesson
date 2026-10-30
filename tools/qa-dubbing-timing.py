"""Measure real cached course speech on a separate copy; no synthesis or API calls."""
from pathlib import Path
import argparse
import hashlib
import json
import os
import shutil
import sys
import uuid

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from mathbridge.application.alignment import cached_alignment_report
from mathbridge.application.dubbing import prepare_timing
from mathbridge.domain.project import segment_fingerprint
from mathbridge.media.engine import MediaEngine
from mathbridge.storage.project_store import ProjectStore,atomic_json
from mathbridge.ui.settings import load_settings

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source',type=Path,default=Path.home()/'Desktop/trans-AI-0.1.3')
    parser.add_argument('--mix',action='store_true')
    args=parser.parse_args()
    source=args.source.resolve()
    original=(source/'project.json').read_bytes()
    project=ProjectStore(source).load()
    settings=load_settings()  # No credential is requested or loaded.
    folder=ROOT/'tmp'/('dubbing-rhythm-'+uuid.uuid4().hex[:10])
    store=ProjectStore(folder); store.save(project)
    print('QA directory:',folder,flush=True)
    for s in project['segments']:
        fp=segment_fingerprint(project,s,settings)
        for suffix in ('.wav','.json'):
            old=source/'audio/tts'/(fp+suffix)
            new=folder/'audio/tts'/old.name
            new.parent.mkdir(parents=True,exist_ok=True)
            try:os.link(old,new)  # Immutable input only; prepared WAVs use new files.
            except OSError:shutil.copyfile(old,new)
    old=cached_alignment_report(project,settings,folder,{'alignment_mode':'strict'})
    engine=MediaEngine()
    audio=folder/'audio/source-16k.wav'
    if (source/'audio/source-16k.wav').exists():
        shutil.copyfile(source/'audio/source-16k.wav',audio)
    else:
        engine.extract_audio(ProjectStore(source).source_path,audio)
    clips=[dict(path=str(folder/'audio/tts'/f'{segment_fingerprint(project,s,settings)}.wav'),
                segment_id=s['id'],start_ms=s['start_ms'],end_ms=s['end_ms'],speaker_id=s['speaker_id'],index=i+1)
           for i,s in enumerate(project['segments'])]
    prepared=prepare_timing(project,settings,folder,clips,audio,
        progress=lambda _s,i,n,m:print(i,'/',n,m,flush=True) if i%50==0 else None)
    report=cached_alignment_report(project,settings,folder)
    report['before_issue_count']=len(old['issues'])
    atomic_json(folder/'audio/alignment-issues.json',report)
    if args.mix and not report['issues']:
        engine.align_and_mix(prepared,folder/'adaptive-dub.wav',project['source']['duration_ms'])
    assert hashlib.sha256((source/'project.json').read_bytes()).digest()==hashlib.sha256(original).digest()
    print(json.dumps({k:report[k] for k in ('before_issue_count','measured_count','pause_compacted_count',
                                          'shifted_count','accelerated_count')},ensure_ascii=False),flush=True)
    print('Remaining issues:',len(report['issues']),flush=True)
    for issue in report['issues'][:12]:print(issue['message'],flush=True)
    print('PASS: original project unchanged; no synthesis or API calls',flush=True)
    return 0

if __name__=='__main__':raise SystemExit(main())
