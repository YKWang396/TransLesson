"""Recheck a real course on a separate copy, retaining the old video for comparison."""
from pathlib import Path
import argparse
import hashlib
import json
import os
import shutil
import sys
import threading
import uuid

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from mathbridge.storage.project_store import ProjectStore,atomic_json
from mathbridge.ui.settings import load_settings
from mathbridge.storage.credentials import load_text_secret
from mathbridge.worker.__main__ import execute
from mathbridge.domain.project import fingerprint,validate_project


def main():
    sys.stdout.reconfigure(encoding='utf8')
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('phase',choices=['setup','boundaries','translate','dub','export'])
    parser.add_argument('--source',type=Path,default=Path.home()/'Desktop/test1-0.1.8验证')
    parser.add_argument('--destination',type=Path,default=Path.home()/'Desktop/test1-0.2.0验证')
    args=parser.parse_args();source=args.source.resolve();root=args.destination.resolve()
    original=(source/'project.json').read_bytes();store=ProjectStore(root)
    def next_revision():
        return max([project['revision']]+[int(p.stem.split('-')[-1]) for p in (root/'history').glob('revision-*.json')])+1
    if args.phase=='setup':
        if root.exists():raise SystemExit('Destination exists; choose a new folder or resume a phase')
        root.mkdir()
        store.save(json.loads(original))
        for folder in ['audio','inputs']:
            for old in (source/folder).rglob('*'):
                if not old.is_file():continue
                new=root/old.relative_to(source);new.parent.mkdir(parents=True,exist_ok=True)
                if old.suffix=='.wav':
                    try:os.link(old,new)
                    except OSError:shutil.copyfile(old,new)
                else:shutil.copyfile(old,new)
        store.write('qa-content.json',dict(source_project_sha256=hashlib.sha256(original).hexdigest(),phases={}))
        print('SETUP',root,flush=True);return
    project=store.load();qa=store.read('qa-content.json');settings=load_settings()
    settings.update(_project_dir=str(root),_request_id='qa020-'+args.phase+'-'+uuid.uuid4().hex[:10],budget_cny=min(5.,settings['budget_cny']))
    def progress(stage,i,n,message):print(stage,i,n,message,flush=True)
    if args.phase in ['boundaries','translate']:
        settings['api_key']=load_text_secret(settings)
        if not settings['api_key']:raise SystemExit('Saved credential unavailable')
    if args.phase=='boundaries':
        from mathbridge.providers.translation.client import TextClient
        from mathbridge.providers.translation.course import review_sentence_boundaries,_checkpoint
        client=TextClient(settings,prompt_version='semantic-boundary-review-2')
        saved,save=_checkpoint(settings,'qa-boundary-review',dict(project=project,version=2))
        before=project['segments']
        project['segments'],mapping=review_sentence_boundaries(before,client,saved,save,progress=progress)
        for note in project['notes']:
            note['segment_ids']=list(dict.fromkeys(mapping[sid] for sid in note['segment_ids']))
        changed=[dict(time_ms=s['start_ms'],before=s['source_text'],after=next(n['source_text'] for n in project['segments'] if n['id']==mapping[s['id']]))
                 for s in before if s['id']!=mapping[s['id']] or next(n['source_text'] for n in project['segments'] if n['id']==mapping[s['id']])!=s['source_text']]
        project['revision']=next_revision();store.save(project)
        qa['phases']['boundaries']=dict(before=len(before),after=len(project['segments']),changes=changed,usage=client.usage)
        print('BOUNDARIES',len(before),'->',len(project['segments']),'changed units',len(changed),flush=True)
    else:
        options=dict(missing_only=True,alignment_mode='adaptive',alignment_max_speed=1.5,alignment_borrow_ms=1000)
        if args.phase=='export':options['formats']=['srt','vtt','wav','video']
        result=execute(dict(stage=args.phase,request_id=settings['_request_id'],project=project,project_dir=str(root),
            source_path=store.source_path,settings=settings,options=options,artifacts=store.read('artifacts.json',[])),
            threading.Event(),lambda event:progress(event.get('stage'),event.get('completed'),event.get('total'),event.get('message')))
        if result.get('project'):
            project=result['project'];project['revision']=next_revision();store.save(project)
        if result.get('artifacts'):store.write('artifacts.json',result['artifacts'])
        qa['phases'][args.phase]=dict(metadata=result.get('metadata',{}),outputs=result.get('outputs',[]))
        runtime=store.read('runtime.json',{'stages':{},'metadata':{}})
        runtime.setdefault('stages',{});runtime.setdefault('metadata',{})
        runtime['stages'][args.phase]={'status':'succeeded'};runtime['metadata'][args.phase]=result.get('metadata',{})
        if args.phase=='dub':
            artifact=next(a for a in result['artifacts'] if a['kind']=='mix')
            runtime['metadata']['dub_master']=dict(path=artifact['path'],input_fingerprint=result['metadata']['alignment']['input_fingerprint'])
        store.write('runtime.json',runtime)
    validate_project(project);store.write('qa-content.json',qa)
    assert (source/'project.json').read_bytes()==original,'Source project changed'
    print('PASS',args.phase,len(project['segments']),'segments',flush=True)


if __name__=='__main__':main()
