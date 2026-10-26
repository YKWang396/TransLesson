import copy
import json
from pathlib import Path

import pytest

from mathbridge.domain.project import changed_copy, export_issues, file_hash, fingerprint, safe_path, segment_fingerprint, validate_project
from mathbridge.storage.project_store import ProjectStore
from mathbridge.application.subtitles import build_cues, import_source_subtitles, write_subtitles
from mathbridge.application.artifacts import validate_render_plan


@pytest.fixture
def project():
    return json.loads((Path(__file__).resolve().parents[1] / 'examples/project.json').read_text(encoding='utf-8'))


def test_schema_and_duplicate_formula_occurrences(project):
    project['segments'][0]['target_text'] += ' 又一次 {{formula-1}}'
    validate_project(project)


@pytest.mark.parametrize('path', ['../secret', 'a/../../b', '/absolute', 'C:/Windows/file', 'a\\b', ''])
def test_path_traversal(tmp_path, path):
    with pytest.raises(ValueError):
        safe_path(tmp_path, path)


def test_overlap_and_fake_sources_rejected(project):
    project['segments'][1]['start_ms'] = 5000
    with pytest.raises(ValueError, match='OVERLAP'):
        validate_project(project)
    project['segments'][1]['start_ms'] = 6500
    project['notes'][0]['segment_ids'] = ['seg-missing']
    with pytest.raises(ValueError, match='REFERENCE'):
        validate_project(project)


def test_revision_history_is_immutable(project, tmp_path):
    store = ProjectStore(tmp_path)
    store.save(project)
    edited = changed_copy(project, 'segments', 'seg-1', {'tts_text': '这是修改后的读法。'})
    store.save(edited)
    assert store.load()['revision'] == 2
    assert json.loads((tmp_path / 'history/revision-000001.json').read_text(encoding='utf-8')) == project
    with pytest.raises(ValueError, match='CONFLICT'):
        store.save({**project, 'title': 'tampered'})


def test_partial_invalidation_uses_content_not_revision(project):
    settings = {'voice': 'test'}
    before = [segment_fingerprint(project, s, settings) for s in project['segments']]
    edited = changed_copy(project, 'segments', 'seg-1', {'tts_text': 'changed'})
    after = [segment_fingerprint(edited, s, settings) for s in edited['segments']]
    assert before[0] != after[0]
    assert before[1] == after[1]
    edited = changed_copy(project, 'segments', 'seg-1', {'subtitle_plain': '只改字幕断行。'})
    assert segment_fingerprint(edited, edited['segments'][0], settings) == before[0]


def test_math_review_gate_and_preserve_source(project):
    project['formulas'][0]['review'] = 'needs_review'
    assert any(i['code'] == 'FORMULA_AMBIGUOUS' for i in export_issues(project))
    project['formulas'][0]['review'] = 'preserve_source'
    assert not [i for i in export_issues(project) if i['severity'] == 'error']


def test_subtitle_round_trip_without_latex(project, tmp_path):
    cues, paths = write_subtitles(project, tmp_path)
    imported = import_source_subtitles(paths['srt'], project['source']['duration_ms'])
    assert len(imported) == len(cues)
    assert all('{{' not in c['text'] for c in cues)
    assert paths['vtt'].read_text(encoding='utf-8').startswith('WEBVTT')


def test_runtime_running_is_recovered(project, tmp_path):
    store = ProjectStore(tmp_path)
    store.save(project)
    store.write('runtime.json', {'stages': {'translate': {'status': 'running'}}})
    store.load()
    assert store.read('runtime.json')['stages']['translate']['status'] == 'interrupted'


def test_illustrative_export_plan_cannot_execute(project, tmp_path):
    plan = json.loads((Path(__file__).resolve().parents[1] / 'examples/render-plan.json').read_text(encoding='utf-8'))
    with pytest.raises(ValueError, match='ILLUSTRATIVE'):
        validate_render_plan(plan, project, tmp_path)


def test_render_manifest_validates_real_hash_and_tamper(project, tmp_path):
    output = tmp_path / 'lesson.srt'
    output.write_text('subtitle', encoding='utf-8')
    artifact = dict(id='subs-test', kind='subtitles', path='lesson.srt', sha256=file_hash(output), input_fingerprint=fingerprint(project), input_revision=1, segment_ids=['seg-1'], depends_on=[])
    plan = dict(schema_version='1.0', illustrative=False, project_id=project['project_id'], project_revision=1, source_sha256=project['source']['sha256'], cues=build_cues(project), artifacts=[artifact], export_artifact_ids=['subs-test'])
    validate_render_plan(plan, project, tmp_path)
    output.write_text('tampered', encoding='utf-8')
    with pytest.raises(ValueError, match='CHANGED'):
        validate_render_plan(plan, project, tmp_path)
