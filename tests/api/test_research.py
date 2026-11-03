import time

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import func, select

from services.api import main
from services.api.db import PatientRecord, SessionRecord, TranscriptSegmentRecord, NoteDraftRecord
from tests.retrieval.test_workspace import add_query, create_research_fixture, grade_all


@pytest.fixture(params=['legacy', 'shared'])
def research_client(tmp_path, monkeypatch, request):
    monkeypatch.setenv('RESEARCH_DATA_DIR', str(tmp_path))
    store = create_research_fixture(tmp_path)
    if request.param == 'shared':
        store.share_annotations('synthetic')
    with TestClient(main.app) as client:
        yield client, store


def counts():
    with main.session_factory() as db:
        return [db.scalar(select(func.count()).select_from(model)) for model in (PatientRecord, SessionRecord, TranscriptSegmentRecord, NoteDraftRecord)]


def clinical_snapshot():
    with main.session_factory() as db:
        return {model.__tablename__: [tuple(getattr(row, col.name) for col in model.__table__.columns)
                                     for row in db.scalars(select(model).order_by(model.id))]
                for model in (PatientRecord, SessionRecord, TranscriptSegmentRecord, NoteDraftRecord)}


def test_research_annotations_never_modify_clinical_records_and_zero_is_explicit(research_client):
    client, store = research_client
    patient = client.post('/api/v1/patients', json={'name': 'Fictional clinical isolation fixture'}).json()
    clinical_session = client.post('/api/v1/sessions', json={'patient_id': patient['id'], 'session_type': 'FOLLOW_UP', 'consent': {}}).json()
    client.get(f"/api/v1/sessions/{clinical_session['id']}/note-draft")
    before = counts()
    original_records = clinical_snapshot()
    assert client.get('/api/v1/research/datasets').json()[0]['dataset_id'] == 'synthetic'
    base = '/api/v1/research/datasets/synthetic/conversations/example'
    detail = client.put(base + '/queries', json={'revision': store.state('synthetic')['revision'], 'text': 'sleep', 'intent': 'Fictional sleep evidence', 'draft': False}).json()
    qid = detail['queries'][0]['query_id']
    assert detail['queries'][0]['judgments'] == {}
    response = client.put(base + f'/queries/{qid}/turns/example-0', json={'revision': detail['revision'], 'grade': 0, 'reviewer': 'test fixture author', 'rationale': 'Fictional irrelevant turn'})
    assert response.status_code == 200
    restored = client.get(base).json()
    assert restored['queries'][0]['judgments']['example-0']['grade'] == 0
    assert restored['queries'][0]['progress']['reviewed'] == 1
    assert client.get('/api/v1/research/datasets/synthetic/export').status_code == 422
    assert client.put(base + f'/queries/{qid}/turns/example-1', json={'revision': restored['revision'], 'grade': True, 'reviewer': 'test', 'rationale': 'test'}).status_code == 422
    assert client.put(base + f'/queries/{qid}/turns/example-1', json={'revision': 0, 'grade': 1, 'reviewer': 'test', 'rationale': 'test'}).status_code == 409
    assert counts() == before
    assert clinical_snapshot() == original_records


def test_export_benchmark_snapshot_and_result_round_trip(research_client):
    client, store = research_client
    detail = grade_all(store, add_query(store), [1, 2])
    response = client.get('/api/v1/research/datasets/synthetic/export?split=dev')
    assert response.status_code == 200
    assert 'attachment' in response.headers['content-disposition']
    exported = response.json()
    assert exported['queries'][0]['query_id'] == detail['queries'][0]['query_id']
    started = client.post('/api/v1/research/datasets/synthetic/runs', json={'split': 'dev', 'methods': ['bm25']})
    assert started.status_code == 202, started.text
    rid = started.json()['run_id']
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        result = client.get(f'/api/v1/research/runs/{rid}').json()
        if result['job']['status'] != 'running':
            break
        time.sleep(.1)
    assert result['job']['status'] == 'completed', result
    report = result['report']
    assert report['results'][0]['status'] == 'measured'
    assert report['query_count'] == 1 and report['split'] == 'dev'
    assert report['dataset_version'] == 'test-v1'
    assert report['evaluation_dataset']['judgments'] == exported['judgments']
    assert report['asr']['status'] == 'not_measured'
    assert report['calibration']['split'] == 'dev'
    # Later edits never rewrite the benchmark's source/annotation snapshot.
    store.grade('synthetic', 'example', detail['queries'][0]['query_id'], 'example-1',
                {'revision': detail['revision'], 'grade': None, 'reviewer': '', 'rationale': ''})
    assert client.get(f'/api/v1/research/runs/{rid}').json()['report'] == report


def test_threshold_and_exploration_do_not_tune_heldout_data(research_client):
    client, store = research_client
    assert client.post('/api/v1/research/datasets/synthetic/runs', json={'split': 'test', 'methods': ['bm25'], 'min_score': .5}).status_code == 422
    assert client.post('/api/v1/research/datasets/synthetic/runs', json={'split': 'dev', 'methods': ['bm25', 'e5'], 'min_score': .5}).status_code == 422
    assert client.post('/api/v1/research/datasets/synthetic/conversations/heldout/explore', json={'revision': 0, 'text': 'sleep'}).status_code == 422
    response = client.post('/api/v1/research/datasets/synthetic/conversations/example/explore', json={'revision': store.state('synthetic')['revision'], 'text': 'sleep'})
    assert response.status_code == 200
    assert response.json()['hits']
    assert 'judgments' not in response.json()
    assert store.state('synthetic')['explored'] == ['example']


def test_existing_fictional_results_are_distinguished_and_include_sources(research_client):
    client, _ = research_client
    response = client.get('/api/v1/research/runs/fictional-smoke')
    assert response.status_code == 200
    report = response.json()['report']
    assert report['result_kind'] == 'fictional'
    assert len(report['results']) == 5
    assert report['query_count'] == 9
    assert report['evaluation_dataset']['conversations'][0]['segments']


def test_import_results_validates_snapshots_and_missing_metrics_remain_missing(research_client):
    client, _ = research_client
    original = client.get('/api/v1/research/runs/fictional-smoke').json()['report']
    original['results'][0] = {'method': 'fixture', 'variant': 'reference', 'status': 'unavailable', 'reason': 'Fictional missing dependency example'}
    response = client.post('/api/v1/research/runs/import', json=original)
    assert response.status_code == 201, response.text
    imported = client.get('/api/v1/research/runs/' + response.json()['run_id']).json()['report']
    assert imported['result_kind'] == 'fictional'
    assert 'quality' not in imported['results'][0]
    assert imported['query_count'] == 9
    broken = {**original, 'snapshot_sha256': 'incorrect-checksum'}
    assert client.post('/api/v1/research/runs/import', json=broken).status_code == 422


def test_frozen_development_configuration_can_be_applied_to_same_method_on_test(research_client):
    client, store = research_client
    grade_all(store, add_query(store), [1, 2])
    grade_all(store, add_query(store, cid='heldout'), [1, 2])
    response = client.post('/api/v1/research/datasets/synthetic/runs', json={'split': 'dev', 'methods': ['bm25'], 'min_score': .3})
    assert response.status_code == 202
    dev_id = response.json()['run_id']
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        dev = client.get('/api/v1/research/runs/' + dev_id).json()
        if dev['job']['status'] != 'running':
            break
        time.sleep(.1)
    assert dev['job']['status'] == 'completed', dev
    assert client.post('/api/v1/research/datasets/synthetic/runs', json={'split': 'test', 'methods': ['e5'], 'frozen_run': dev_id}).status_code == 422
    response = client.post('/api/v1/research/datasets/synthetic/runs', json={'split': 'test', 'methods': ['bm25'], 'frozen_run': dev_id})
    assert response.status_code == 202, response.text
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        result = client.get('/api/v1/research/runs/' + response.json()['run_id']).json()
        if result['job']['status'] != 'running':
            break
        time.sleep(.1)
    assert result['job']['status'] == 'completed', result
    assert result['report']['settings']['min_score'] == .3
    assert result['report']['calibration'] == dev['report']['calibration']
    assert result['report']['split'] == 'test'


def test_blind_final_form_uses_only_the_research_workspace(research_client):
    client, store = research_client
    assert client.get('/api/v1/research/independent-final').status_code == 404
    path = store.root / 'review-experiments/independent-final/review.html'
    path.parent.mkdir(parents=True)
    path.write_text('<!doctype html><h1>Fictional blind packet test</h1>')
    response = client.get('/api/v1/research/independent-final')
    assert response.status_code == 200
    assert response.headers['content-type'].startswith('text/html')
    assert 'Fictional blind packet test' in response.text
