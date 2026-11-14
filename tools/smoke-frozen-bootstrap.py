"""Check packaged native imports with an open cancellation pipe and no TTS call."""
from pathlib import Path
import argparse
import json
import os
import queue
import subprocess
import sys
import threading
import time
import uuid

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'src'))
from mathbridge.storage.project_store import ProjectStore
from mathbridge.ui.settings import load_settings


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--project',type=Path,required=True)
    args=parser.parse_args();project=ProjectStore(args.project).load();settings=load_settings()
    assert any(s['voice_mode']=='reference' for s in project['speakers'])
    assert Path(settings['whisper_model_path'],'model.bin').is_file()
    environment=dict(os.environ,PATH=str(Path(os.environ['WINDIR'])/'System32'),PYTHONIOENCODING='utf-8',
                     PYTHONUTF8='1',HF_HUB_OFFLINE='1',TRANSFORMERS_OFFLINE='1')
    environment.pop('PYTHONPATH',None)
    for stage in ('dub','preview','export'):
        folder=ROOT/'tmp'/('frozen-bootstrap-'+uuid.uuid4().hex[:10]);folder.mkdir()
        command=dict(stage=stage,request_id='req-native-'+uuid.uuid4().hex,project=project,project_dir=str(folder),
            source_path=str(folder/'missing-source.mp4'),settings=settings,options=dict(formats=['video']))
        records=queue.Queue()
        process=subprocess.Popen([str(ROOT/'dist/TransLesson/translesson-worker.exe'),'--worker'],stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,text=True,encoding='utf8',env=environment,cwd=folder,creationflags=0x08000000)
        reader=threading.Thread(target=lambda:[records.put(json.loads(line)) for line in process.stdout],daemon=True);reader.start()
        try:
            process.stdin.write(json.dumps(command,ensure_ascii=False)+'\n');process.stdin.flush()
            deadline=time.monotonic()+25;done=None;loaded=False
            while time.monotonic()<deadline:
                try:message=records.get(timeout=.1)
                except queue.Empty:continue
                loaded |= message.get('type')=='progress' and '配音检查' in message.get('message','')
                if message.get('type')=='done':done=message;break
            assert loaded and done,'Native imports stalled with cancellation pipe open: '+stage
            assert done['status']=='failed' and 'SOURCE_MISSING' in done.get('message',''),done
            print('PASS packaged native bootstrap',stage,flush=True)
        finally:
            process.stdin.close()
            try:process.wait(timeout=3)
            except subprocess.TimeoutExpired:process.kill();process.wait(timeout=3)
            reader.join(2);process.stdout.close()


if __name__=='__main__':main()
