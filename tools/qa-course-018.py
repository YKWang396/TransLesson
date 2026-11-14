"""Validate semantic sentences and reference audio on a separate real-course copy.

Uses the user's saved text API settings and credential in memory only. Original
course files are read only. Each completed stage and paid request is checkpointed.
"""
from pathlib import Path
import argparse
import copy
import hashlib
import json
import shutil
import sys
import threading

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from mathbridge.storage.project_store import ProjectStore,atomic_json
from mathbridge.storage.credentials import load_text_secret
from mathbridge.ui.settings import load_settings
from mathbridge.worker.__main__ import execute
from mathbridge.application.reference import load_reference_words,suggest_auxiliary_references
from mathbridge.application.alignment import plan_alignment
from mathbridge.domain.project import validate_project


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('phase',choices=['setup','sentences','translate','voices','dub','export'])
    parser.add_argument('--speed',type=float,default=1.15,choices=[1.15,1.35,1.5])
    args=parser.parse_args()
    source=Path.home()/'Desktop/test1'
    destination=Path.home()/'Desktop/test1-0.1.8验证'
    original=(source/'project.json').read_bytes()
    store=ProjectStore(destination)
    if args.phase=='setup':
        if (destination/'project.json').exists():
            raise SystemExit('QA project already exists; reuse completed phases explicitly')
        destination.mkdir()
        for name in ['audio/source-16k.wav','audio/source-16k.json','audio/asr-raw.json','inputs/source.link.json']:
            old=source/name;new=destination/name
            new.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(old,new)
        store.save(json.loads(original))
        store.write('qa-018.json',dict(original_project_sha256=hashlib.sha256(original).hexdigest(),phases={}))
        old=json.loads((source/'audio/alignment-issues.json').read_text('utf8'))
        newplans,newissues=plan_alignment(old['plans'],json.loads(original)['source']['duration_ms'],
            dict(alignment_max_speed=1.15,alignment_mode='adaptive'))
        oldplans=old['plans']
        comparison=dict(old_speed_cap=old['options']['alignment_max_speed'],old_accelerated=sum(p['speed']>1.005 for p in oldplans),
            new_speed_cap=1.15,new_accelerated=sum(p['speed']>1.005 for p in newplans),new_issues=len(newissues),
            new_borrowed_before=sum(p.get('borrowed_before_ms',0)>20 for p in newplans),
            new_long_tail=sum(p['available_end_ms']-p['end_ms']>2000 for p in newplans))
        store.write('qa-existing-timing.json',comparison)
        print('SETUP',destination,comparison,flush=True)
        return
    project=json.loads((destination/'project.json').read_text('utf8'))
    qa=store.read('qa-018.json')
    settings=load_settings()
    settings.update(_project_dir=str(destination),_request_id='qa018-'+args.phase)
    if args.phase in ('sentences','translate'):
        settings['api_key']=load_text_secret(settings)
        if not settings['api_key']:raise SystemExit('Saved credential unavailable')
    if args.phase=='voices':
        for speaker in project['speakers']:
            if speaker['voice_mode']=='reference':
                profile=speaker['voice_profile']
                primary=dict(start_ms=profile['reference_start_ms'],end_ms=profile['reference_end_ms'])
                profile['auxiliary_references']=suggest_auxiliary_references(project,speaker['id'],load_reference_words(project,destination),primary)
        project['revision']+=1;store.save(project)
        qa['phases']['voices']={'references':[{k:v for k,v in s['voice_profile'].items() if k in ('reference_start_ms','reference_end_ms','auxiliary_references')} for s in project['speakers'] if s.get('voice_profile')]}
    else:
        def report(event):
            if event.get('type')=='progress':
                print(event.get('stage'),event.get('completed'),event.get('total'),event.get('message'),flush=True)
        options=dict(missing_only=True,alignment_mode='adaptive',alignment_max_speed=args.speed,alignment_borrow_ms=1000)
        if args.phase=='export':options['formats']=['srt','vtt','wav','video']
        command=dict(request_id=settings['_request_id'],stage=args.phase,project=project,project_dir=str(destination),
                     source_path=ProjectStore(destination).source_path,settings=settings,options=options,artifacts=store.read('artifacts.json',[]))
        result=execute(command,threading.Event(),report)
        if result.get('project'):
            candidate=result['project']
            if candidate != project:candidate['revision']=project['revision']+1
            project=candidate;store.save(project)
        qa['phases'][args.phase]=result.get('metadata',{})
        if result.get('artifacts'):
            store.write('artifacts.json',result['artifacts'])
        if result.get('outputs'):
            qa['phases'][args.phase]['outputs']=result['outputs']
        runtime=store.read('runtime.json',{'stages':{}})
        runtime.setdefault('stages',{})[args.phase]={'status':'succeeded'}
        runtime.setdefault('metadata',{})[args.phase]=result.get('metadata',{})
        if args.phase=='dub' and result.get('metadata',{}).get('alignment'):
            mix=next(a for a in result['artifacts'] if a['kind']=='mix')
            runtime['metadata']['dub_master']=dict(path=mix['path'],input_fingerprint=result['metadata']['alignment']['input_fingerprint'])
        store.write('runtime.json',runtime)
        if result.get('metadata',{}).get('usage'):
            from mathbridge.application.coordinator import Coordinator
            Coordinator()._record_usage(store,result['metadata']['usage'],settings['_request_id'])
    validate_project(project)
    assert (source/'project.json').read_bytes()==original,'Original course changed concurrently'
    store.write('qa-018.json',qa)
    print('PASS',args.phase,'segments',len(project['segments']),'revision',project['revision'],flush=True)


if __name__=='__main__':main()
