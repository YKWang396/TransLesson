from mathbridge.application.coordinator import Coordinator


def test_pipeline_keeps_user_timing_limits_between_workers(monkeypatch):
    from mathbridge.application import coordinator as module
    coordinator=Coordinator()
    coordinator._done=True
    coordinator._cancelled=False
    coordinator._pipeline=['dub']
    options=dict(alignment_mode='adaptive',alignment_max_speed=1.35,alignment_borrow_ms=2000,sample_frames=True)
    coordinator._command=dict(options=options)
    coordinator._read_output=lambda:None
    class Process:
        def deleteLater(self):pass
    coordinator._process=Process()
    starts=[]
    coordinator._start=lambda stage,options:starts.append((stage,options))
    monkeypatch.setattr(module.QTimer,'singleShot',lambda _delay,callback:callback())
    coordinator._process_finished(0,None)
    assert starts==[('dub',dict(alignment_mode='adaptive',alignment_max_speed=1.35,alignment_borrow_ms=2000))]
