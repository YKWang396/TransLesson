import pytest
from mathbridge.application.coordinator import Coordinator
from mathbridge.storage.project_store import ProjectStore


def test_interrupted_request_reservation_is_recovered_and_settled_idempotently(tmp_path):
    store = ProjectStore(tmp_path)
    request_id = 'req-interrupted-test'
    store.write(f'cache/api-reservations/{request_id}.json', {
        'request_id': request_id, 'usage': {'calls': 1, 'cost_cny': 0.02},
        'pending_cost_cny': 0.5, 'pending_calls': 0})
    coordinator = Coordinator()
    coordinator._recover_reservations(store)
    assert coordinator.usage['reserved_total_cny'] == pytest.approx(0.52)
    assert coordinator.usage['uncertain_requests'] == 1
    coordinator._recover_reservations(store)
    assert coordinator.usage['calls'] == 1
    assert coordinator.usage['reserved_total_cny'] == pytest.approx(0.52)
    # A response can reduce a conservative pending reservation to actual cost.
    store.write(f'cache/api-reservations/{request_id}.json', {
        'request_id': request_id, 'usage': {'calls': 1, 'cost_cny': 0.04},
        'pending_cost_cny': 0, 'pending_calls': 0})
    coordinator._recover_reservations(store)
    assert coordinator.usage['cost_cny'] == pytest.approx(0.04)
    assert coordinator.usage['reserved_total_cny'] == pytest.approx(0.04)
    assert coordinator.usage['uncertain_requests'] == 0


def test_translation_edit_requires_matching_plain_and_spoken_text(tmp_path):
    import json
    from pathlib import Path
    project = json.loads((Path(__file__).parents[1] / 'examples/project.json').read_text(encoding='utf-8'))
    store = ProjectStore(tmp_path)
    store.save(project)
    coordinator = Coordinator()
    coordinator.open_project(str(tmp_path))
    coordinator.update_segment('seg-2', {'target_text': '随后计算该多项式的导数。'})
    segment = coordinator.project['segments'][1]
    assert segment['tts_text'] == '' and segment['subtitle_plain'] == ''
    assert segment['review'] == 'needs_review'
    assert any(i['code'] == 'MISSING_TRANSLATION' for i in coordinator.export_issues())
