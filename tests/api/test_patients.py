from datetime import date, timedelta
from fastapi.testclient import TestClient
from services.api.main import app
from tests.api.helpers import seed_segment


def test_patient_intake_edit_reopen_and_session_isolation():
    with TestClient(app) as client:
        response = client.post('/api/v1/patients', json={
            'name': '  Fictional Patient  ', 'age': 28, 'sex': 'Female',
            'phone': '555-0100', 'presenting_concerns': 'Fictional intake concern.',
            'emergency_contact': 'Fictional Contact', 'medications': 'Not yet discussed',
        })
        assert response.status_code == 201, response.text
        patient = response.json()
        assert patient['name'] == 'Fictional Patient'
        second = client.post('/api/v1/patients', json={'name': 'Other Fictional Patient'}).json()
        # Similar names never imply shared identity.
        same_name = client.post('/api/v1/patients', json={'name': 'Fictional Patient'}).json()
        assert same_name['id'] != patient['id']
        first_session = client.post('/api/v1/sessions', json={
            'patient_id': patient['id'], 'session_type': 'INITIAL_ASSESSMENT', 'consent': {},
        }).json()
        follow_up = client.post('/api/v1/sessions', json={
            'patient_id': patient['id'], 'session_type': 'FOLLOW_UP', 'consent': {},
        }).json()
        other_session = client.post('/api/v1/sessions', json={
            'patient_id': second['id'], 'session_type': 'FOLLOW_UP', 'consent': {},
        }).json()
        sessions = client.get(f"/api/v1/patients/{patient['id']}/sessions").json()
        assert [s['id'] for s in sessions] == [follow_up['id'], first_session['id']]
        assert all(s['patient_id'] == patient['id'] for s in sessions)
        assert other_session['id'] not in [s['id'] for s in sessions]
        assert client.get(f"/api/v1/sessions/{first_session['id']}/transcript").json() == []
        data = {k: v for k, v in patient.items() if k not in ('id', 'created_at', 'updated_at')}
        data.update(preferred_name='Preferred name', goals='Fictional goals')
        assert client.put(f"/api/v1/patients/{patient['id']}", json=data).status_code == 200
    with TestClient(app) as client:
        reopened = client.get(f"/api/v1/patients/{patient['id']}").json()
        assert reopened['preferred_name'] == 'Preferred name'
        assert reopened['phone'] == '555-0100'
        assert reopened['presenting_concerns'] == 'Fictional intake concern.'
        assert len(client.get(f"/api/v1/patients/{patient['id']}/sessions").json()) == 2


def test_patient_validation_and_missing_records():
    with TestClient(app) as client:
        for data in ({'name': '   '}, {'name': 'Test', 'age': -1}, {'name': 'Test', 'age': 121},
                     {'name': 'Test', 'date_of_birth': str(date.today() + timedelta(days=1))},
                     {'name': 'Test', 'date_of_birth': '2000-01-01', 'age': 1}):
            assert client.post('/api/v1/patients', json=data).status_code == 422
        response = client.post('/api/v1/patients', json={'name': 'Test DOB', 'date_of_birth': '2000-01-01'})
        assert response.status_code == 201 and response.json()['age'] is None
        assert client.get('/api/v1/patients/missing').status_code == 404
        assert client.get('/api/v1/patients/missing/sessions').status_code == 404
        assert client.post('/api/v1/sessions', json={
            'patient_id': 'missing', 'session_type': 'FOLLOW_UP', 'consent': {},
        }).status_code == 404


def test_link_existing_session_preserves_transcript_and_cannot_reassign():
    with TestClient(app) as client:
        patient = client.post('/api/v1/patients', json={'name': 'Legacy Fictional Patient'}).json()
        other = client.post('/api/v1/patients', json={'name': 'Different Fictional Patient'}).json()
        session = client.post('/api/v1/sessions', json={'session_type': 'FOLLOW_UP', 'consent': {}}).json()
        assert session['patient_id'] is None
        seed_segment(session['id'])
        transcript = client.get(f"/api/v1/sessions/{session['id']}/transcript").json()
        linked = client.patch(f"/api/v1/sessions/{session['id']}/patient", json={'patient_id': patient['id']})
        assert linked.status_code == 200
        assert linked.json()['patient_id'] == patient['id']
        assert client.get(f"/api/v1/sessions/{session['id']}/transcript").json() == transcript
        assert client.get(f"/api/v1/patients/{patient['id']}/sessions").json()[0]['id'] == session['id']
        assert client.patch(f"/api/v1/sessions/{session['id']}/patient", json={'patient_id': other['id']}).status_code == 409
