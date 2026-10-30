"""Check reference auto-fill, preservation and time/text synchronization."""
from pathlib import Path
import copy
import json
import os
import sys
import uuid

os.environ.setdefault("QT_QPA_PLATFORM","offscreen")
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"src"))
from PySide6.QtGui import QFontDatabase
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from mathbridge.application.reference import load_reference_words,reference_for_range,suggest_reference
from mathbridge.ui.editors import SpeakerDialog

def main():
    app=QApplication([]);app.setStyle("Fusion")
    for name in ("msyh.ttc","msyhbd.ttc"):
        QFontDatabase.addApplicationFont(str(Path(os.environ.get("WINDIR","C:/Windows"))/"Fonts"/name))
    project=dict(source=dict(duration_ms=30000),languages=dict(source="en"),segments=[
        dict(id="a",speaker_id="teacher",start_ms=1000,end_ms=7000,source_text="This is the first source sentence."),
        dict(id="b",speaker_id="teacher",start_ms=10000,end_ms=16000,source_text="This is another source sentence.")])
    speaker=dict(id="teacher",label="主讲人",voice_mode="standard")
    candidate=suggest_reference(project,"teacher",preferred_segment_id="a")
    calls=[]
    dialog=SpeakerDialog(speaker,"en",30000,candidate,
        reference_lookup=lambda a,b:reference_for_range(project,"teacher",a,b),
        replay_reference=lambda *args:calls.append(args))
    dialog.provider.setCurrentIndex(dialog.provider.findData("gpt_sovits"))
    assert dialog.reference_text.toPlainText()==project["segments"][0]["source_text"]
    assert dialog.reference_start.value()==1 and dialog.reference_end.value()==7
    assert not dialog.authorized.isChecked()
    dialog.replay_reference_button.click();assert calls==[(1000,7000)]
    dialog.reference_start.setValue(10);dialog.reference_end.setValue(16)
    QTest.qWait(250)
    assert dialog.reference_text.toPlainText()==project["segments"][1]["source_text"]
    dialog.reference_end.setValue(15);QTest.qWait(250)
    assert not dialog.reference_text.toPlainText()
    dialog.auto_reference_button.click()
    assert dialog.reference_start.value()==1 and dialog.reference_end.value()==7
    dialog.reference_end.setValue(6)
    dialog.reference_text.setPlainText("Explicit manual correction takes priority.")
    QTest.qWait(250)
    assert dialog.reference_text.toPlainText()=="Explicit manual correction takes priority."
    dialog.close()
    saved=copy.deepcopy(speaker)
    saved.update(voice_mode="reference",voice_profile=dict(provider="gpt_sovits",reference_start_ms=2000,
        reference_end_ms=8000,reference_text="Existing manually corrected reference.",reference_language="en"))
    dialog=SpeakerDialog(saved,"en",30000,candidate)
    assert dialog.reference_start.value()==2 and dialog.reference_end.value()==8
    assert dialog.reference_text.toPlainText()==saved["voice_profile"]["reference_text"]
    dialog.close()
    source=Path.home()/"Desktop/trans-AI-0.1.3"
    original=(source/"project.json").read_bytes()
    project=json.loads(original)
    words=load_reference_words(project,source)
    candidate=suggest_reference(project,"teacher",words)
    dialog=SpeakerDialog(project["speakers"][0],project["languages"]["source"],project["source"]["duration_ms"],candidate,
        reference_lookup=lambda a,b:reference_for_range(project,"teacher",a,b,words),replay_reference=lambda *_:None)
    dialog.provider.setCurrentIndex(dialog.provider.findData("gpt_sovits"))
    dialog.show();QTest.qWait(100)
    (ROOT/"artifacts").mkdir(exist_ok=True)
    dialog.grab().save(str(ROOT/"artifacts/ui-auto-reference.png"))
    assert dialog.reference_text.toPlainText()
    dialog.close()
    assert (source/"project.json").read_bytes()==original
    print("PASS: automatic original text/time/language, replay, timer sync, stale text clearing, manual preservation, source course unchanged")
    return 0

if __name__=="__main__":raise SystemExit(main())
