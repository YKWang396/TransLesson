"""Opt-in local TTS/ASR checks across subjects; no text API or remote audio."""
from pathlib import Path
import argparse
import json
import sys
import uuid

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from mathbridge.storage.project_store import ProjectStore,atomic_json
from mathbridge.ui.settings import load_settings
from mathbridge.application.voices import prepare_reference,prepare_auxiliary_references,cached_target_reference
from mathbridge.application.speech_quality import SpeechVerifier
from mathbridge.media.engine import MediaEngine
from mathbridge.providers.tts.gpt_sovits import synthesize


def main():
    sys.stdout.reconfigure(encoding='utf8')
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project',type=Path,required=True)
    parser.add_argument('--real',action='store_true')
    args=parser.parse_args()
    if not args.real:raise SystemExit('Pass --real to use the local voice service and model.')
    store=ProjectStore(args.project);project=store.load();settings=load_settings()
    speaker=next(s for s in project['speakers'] if s['voice_mode']=='reference' and s['voice_authorized'])
    profile=speaker['voice_profile'];engine=MediaEngine()
    params=dict(source_media=store.source_path,media_engine=engine)
    reference=prepare_reference(project,speaker,store.root,store.root/'audio/source-16k.wav',**params)
    auxiliary=prepare_auxiliary_references(project,speaker,store.root,store.root/'audio/source-16k.wav',**params)
    fallback=lambda:cached_target_reference(project,speaker['id'],'smoke-unrelated',store.root,settings)
    verifier=SpeechVerifier(settings)
    root=ROOT/'tmp'/('speech-content-'+uuid.uuid4().hex[:8]);root.mkdir(parents=True)
    rows=[]
    examples=[('short','zh-CN','对吗？'),
      ('biology','zh-CN','光合作用发生在叶绿体中，叶绿素吸收光能并参与能量转换。'),
      ('history','zh-CN','研究历史材料时，我们要比较作者的立场、写作年代和材料之间的联系。'),
      ('physics','zh-CN','如果速度保持不变，路程等于速度乘以时间；这里的条件不能省略。'),
      ('math','zh-CN','二加三等于五；矩阵乘法还必须检查行数和列数。'),
      ('negation','zh-CN','不要把所有变量同时增加，这会让我们无法判断变化的原因。'),
      ('english','en','If the velocity stays constant, distance equals velocity multiplied by time.')]
    for name,language,text in examples:
        quality={};retries=[];path=root/(name+'.wav')
        def verify(candidate):quality.update(verifier.verify(candidate,text,language))
        synthesize(text,language,reference,profile['reference_text'],profile['reference_language'],path,
                   endpoint=settings['voice_clone_api_url'],auxiliary_reference_audio=auxiliary,
                   verify=verify,retry_notice=lambda attempt,code:retries.append(dict(attempt=attempt,code=code)),
                   fallback_reference=fallback if language=='zh-CN' else None)
        row=dict(case=name,text=text,language=language,quality=quality,retries=retries,path=str(path))
        rows.append(row);atomic_json(root/'report.json',dict(examples=rows))
        print('PASS',name,json.dumps(quality,ensure_ascii=False),'retries',len(retries),flush=True)
    print('PASS local cross-subject content checks',root,flush=True)


if __name__=='__main__':main()
