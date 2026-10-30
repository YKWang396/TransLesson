"""Verify the installed frozen worker loads its adjacent Whisper model offline."""
from pathlib import Path
import json
import os
import subprocess
import sys
import uuid

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'src'))
from mathbridge.application.components import component_paths
from mathbridge.domain.project import new_project,file_hash
from mathbridge.media.engine import MediaEngine
from mathbridge.storage.project_store import ProjectStore


def main():
    install=Path.home()/'Desktop/课译TransLesson'
    source=ProjectStore(Path.home()/'Desktop/test1').source_path
    root=ROOT/'tmp'/('installed-components-'+uuid.uuid4().hex[:8]);root.mkdir()
    wav=root/'sample.wav'
    MediaEngine()._ffmpeg(['-i',source,'-t','20','-vn','-ac','1','-ar','16000','-c:a','pcm_s16le',wav],stage='prepare')
    project=new_project('组件识别验证',dict(path='inputs/source.link.json',sha256=file_hash(wav),duration_ms=20000,timeline_origin_ms=0))
    store=ProjectStore(root);store.save(project)
    settings=dict(component_paths(install/'components'),whisper_device='cpu',whisper_compute_type='int8')
    command=dict(type='command',request_id='req-installed-'+uuid.uuid4().hex,stage='transcribe',project=project,
                 project_revision=project['revision'],project_dir=str(root),source_path=str(wav),settings=settings,options={})
    environment=os.environ.copy();environment['PATH']=str(Path(os.environ['WINDIR'])/'System32')
    environment['HF_HUB_OFFLINE']='1';environment['TRANSFORMERS_OFFLINE']='1';environment.pop('PYTHONPATH',None)
    result=subprocess.run([str(install/'translesson-worker.exe'),'--worker'],input=json.dumps(command,ensure_ascii=False)+'\n',
        text=True,encoding='utf8',capture_output=True,env=environment,cwd=install,timeout=180)
    (root/'worker.stderr.txt').write_text(result.stderr,encoding='utf8')
    messages=[json.loads(line) for line in result.stdout.splitlines() if line.strip()]
    done=next((m for m in messages if m.get('type')=='done'),{})
    assert done.get('status')=='succeeded',done
    segments=done['result']['project']['segments'];assert segments and any(s['source_text'].strip() for s in segments)
    print('PASS: installed frozen worker, adjacent Unicode component path, CPU int8, offline Whisper ASR')
    print('Recognized:', ' '.join(s['source_text'] for s in segments))


if __name__=='__main__':main()
