import json

import pytest

from app.comsol.bridge_stream import unavailable_event


@pytest.mark.parametrize('state', ['idle', 'stopped', 'starting', 'failed', 'crashed', 'ready', None])
def test_offline_status_never_certifies_a_model_or_masks_an_actual_failure(state):
    event = unavailable_event({'state': state, 'running': state == 'ready'}, 17)
    assert event['sequence'] == 17
    assert event['event_type'] == ('command' if state in {'idle', 'stopped', 'starting'} else 'critical_failure')
    assert not any(event[key] for key in ('command_success', 'model_state_verified', 'scientific_validation_passed'))


@pytest.mark.asyncio
async def test_stream_emits_safe_offline_state_and_stops_for_desktop_shutdown(monkeypatch):
    import app.main as main

    async def unavailable(*args):
        raise ValueError('secret=private-password C:/private/model.mph')

    monkeypatch.setattr(main.java_bridge_client, 'events', unavailable)
    monkeypatch.setattr(main.java_bridge_process_manager, 'status', lambda: {'state': 'stopped', 'running': False})
    monkeypatch.setattr(main.app.state, 'desktop_stopping', False, raising=False)
    response = await main.java_bridge_stream(0, None)
    frame = await anext(response.body_iterator)
    event = json.loads(frame.removeprefix('data: '))
    assert event['event_type'] == 'command' and event['status'] == 'stopped'
    assert 'private' not in frame and 'password' not in frame
    monkeypatch.setattr(main.java_bridge_process_manager, 'status', lambda: {'state': 'failed', 'running': False})
    failure = json.loads((await anext(response.body_iterator)).removeprefix('data: '))
    assert failure['event_type'] == 'critical_failure'
    main.app.state.desktop_stopping = True
    with pytest.raises(StopAsyncIteration):
        await anext(response.body_iterator)
