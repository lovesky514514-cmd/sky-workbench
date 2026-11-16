"""事件流区分正常离线和意外断连，不向前端回显异常正文。"""


def unavailable_event(process: dict, sequence: int) -> dict:
    state = process.get('state')
    expected = not process.get('running') and state in {'idle', 'stopped', 'starting'}
    return {'sequence': sequence, 'event_type': 'command' if expected else 'critical_failure',
            'phase': 'bridge', 'status': state if expected else 'unavailable',
            'detail': ('Bridge is not connected; model readback is unavailable.' if expected else
                       'Bridge connection unavailable; inspect scoped diagnostic records.'),
            'command_success': False, 'model_state_verified': False, 'scientific_validation_passed': False}
