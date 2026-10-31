"""Exercise the packaged speech verifier and local TTS with developer PATH removed."""
from pathlib import Path
import argparse
import json
import os
import shutil
import subprocess
import sys
import uuid

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from mathbridge.storage.project_store import ProjectStore,atomic_json
from mathbridge.ui.settings import load_settings


def main():
    sys.stdout.reconfigure(encoding='utf8')
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project',type=Path,required=True)
    args=parser.parse_args();source=ProjectStore(args.project);project=source.load();settings=load_settings()
    root=ROOT/'tmp'/('frozen-speech-'+uuid.uuid4().hex[:8]);root.mkdir(parents=True)
    project['segments']=project['segments'][:1]
    segment=project['segments'][0]
    text='如果温度保持不变，这个实验条件必须保留。'
    segment.update(source_text='If the temperature stays constant, this experimental condition must be retained.',
                   target_text=text,tts_text=text,subtitle_plain=text,formula_ids=[],term_ids=[])
    project['notes']=[];project['formulas']=[];project['glossary']=[]
    store=ProjectStore(root);store.save(project)
    for relative in ['audio/source-16k.wav','audio/source-16k.json']:
        old=source.root/relative;new=root/relative;new.parent.mkdir(parents=True,exist_ok=True)
        if old.suffix=='.wav':
            try:os.link(old,new)
            except OSError:shutil.copyfile(old,new)
        else:shutil.copyfile(old,new)
    shutil.copytree(source.root/'audio/references',root/'audio/references')
    command=dict(request_id='qa-frozen-speech',stage='dub',project=project,project_dir=str(root),
                 source_path=source.source_path,settings=settings,options=dict(sample_only=True))
    env=os.environ.copy();env.pop('PYTHONPATH',None)
    env.update(PATH=str(Path(os.environ['WINDIR'])/'System32'),HF_HUB_OFFLINE='1',TRANSFORMERS_OFFLINE='1',PYTHONIOENCODING='utf-8')
    result=subprocess.run([str(ROOT/'dist/TransLesson/translesson-worker.exe'),'--worker'],
        input=json.dumps(command,ensure_ascii=False)+'\n',capture_output=True,text=True,encoding='utf8',
        env=env,cwd=root,timeout=240,creationflags=0x08000000)
    messages=[json.loads(line) for line in result.stdout.splitlines() if line.strip()]
    done=next(m for m in messages if m['type']=='done')
    if done['status']!='succeeded':raise RuntimeError(str(done))
    artifact=done['result']['artifacts'][0]
    quality=json.loads((root/artifact['path']).with_suffix('.json').read_text('utf8'))['quality']
    assert quality['method']=='local-asr' and not quality['suspected_omission']
    atomic_json(root/'verification.json',dict(quality=quality,worker_result=done))
    print('PASS frozen local TTS and ASR verifier; isolated PATH; adjacent Chinese component directory',json.dumps(quality,ensure_ascii=False),root,flush=True)


if __name__=='__main__':main()
