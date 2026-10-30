from pathlib import Path
import pytest

from mathbridge.application import components
from mathbridge.ui.voice_service import service_arguments
from mathbridge.media.engine import MediaError


def component_tree(root):
    for name in ('faster-whisper-small/model.bin','faster-whisper-small/config.json','faster-whisper-small/tokenizer.json',
                 'GPT-SoVITS/runtime/python.exe','GPT-SoVITS/api_v2.py','GPT-SoVITS/GPT_SoVITS/configs/tts_infer.yaml'):
        path=root/name;path.parent.mkdir(parents=True,exist_ok=True);path.touch()


def test_portable_copy_uses_its_own_components_instead_of_other_computer_paths(tmp_path,monkeypatch):
    install=tmp_path/'new computer 课译TransLesson';component_tree(install/'components')
    monkeypatch.setattr(components,'application_directory',lambda:install)
    settings=components.resolve_component_settings(dict(components_root='Z:/old app/components',whisper_model_path='Z:/old model',gpt_sovits_path='Z:/old voice'))
    assert Path(settings['whisper_model_path'])==install/'components/faster-whisper-small'
    assert Path(settings['gpt_sovits_path'])==install/'components/GPT-SoVITS'


def test_external_component_locations_are_preserved_when_no_bundle_is_present(tmp_path,monkeypatch):
    external=tmp_path/'external components';component_tree(external)
    monkeypatch.setattr(components,'application_directory',lambda:tmp_path/'app')
    settings=components.resolve_component_settings(dict(components_root=str(external)))
    assert components.whisper_available(settings['whisper_model_path'])
    assert components.gpt_sovits_available(settings['gpt_sovits_path'])


def test_explicit_custom_root_remains_selected_even_when_local_components_exist(tmp_path,monkeypatch):
    install=tmp_path/'app';external=tmp_path/'custom components'
    component_tree(install/'components');component_tree(external)
    monkeypatch.setattr(components,'application_directory',lambda:install)
    settings=components.resolve_component_settings(dict(components_root=str(external)))
    assert Path(settings['whisper_model_path'])==external/'faster-whisper-small'
    assert Path(settings['gpt_sovits_path'])==external/'GPT-SoVITS'


def test_service_arguments_keep_unicode_spaces_separate_and_bind_only_loopback(tmp_path):
    component_tree(tmp_path/'中文 安装/components')
    root=tmp_path/'中文 安装/components/GPT-SoVITS'
    args=service_arguments(dict(gpt_sovits_path=str(root),voice_clone_api_url='http://localhost:9891/tts'),'start')
    assert args[args.index('-BundleRoot')+1]==str(root.resolve())
    assert args[args.index('-Address')+1]=='127.0.0.1'
    assert args[args.index('-Port')+1]=='9891'
    with pytest.raises(MediaError):
        service_arguments(dict(gpt_sovits_path=str(root),voice_clone_api_url='http://example.com:9891'),'start')
    with pytest.raises(ValueError):service_arguments(dict(gpt_sovits_path=str(tmp_path/'missing')),'start')
