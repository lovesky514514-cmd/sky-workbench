from pathlib import Path

from app.models import AgentMode, AgentState
from app.session.store import SessionStore


def test_session_event_log_restores_messages_and_timeline(tmp_path: Path):
    store = SessionStore(tmp_path / 'sessions.db')
    store.initialize()
    session = store.create('workbench')
    store.add_message(session['id'], 'user', 'heat model', AgentMode.PLAN)
    store.set_state(session['id'], AgentState.RESEARCHING)
    store.append_event(
        session['id'],
        'timeline.started',
        {'id': 'step-1', 'phase': 'Research', 'label': 'Search', 'status': 'running'},
    )
    store.append_event(
        session['id'],
        'timeline.completed',
        {'id': 'step-1', 'status': 'success', 'duration_ms': 3},
    )
    restored = store.get(session['id'])
    assert restored['messages'][0]['content'] == 'heat model'
    assert restored['timeline'] == [
        {'id': 'step-1', 'phase': 'Research', 'label': 'Search', 'status': 'success', 'duration_ms': 3}
    ]

