"""覆盖提供的状态表，以及跨方法、引擎、提交状态的禁止重放约束。"""
import json
from pathlib import Path
import socket
import ssl

import httpx
import pytest
from pydantic import ValidationError

from app.recovery.http_contracts import CommitState, HttpAction, HttpObservation, OperationKind, TransportFailure, bridge_operation
from app.recovery.http_policy import PROVIDER_CODES, STATUS_ACTIONS, decide, parse_retry_after, transport_failure


REFERENCE = json.loads((Path(__file__).parent / 'fixtures/http_status_policy_v1.json').read_text(encoding='utf-8'))


@pytest.mark.parametrize('row', REFERENCE, ids=lambda r: str(r['status']))
def test_full_supplied_iana_policy(row):
    code = row['status']
    assert STATUS_ACTIONS[code] == row['initial_action']
    result = decide(HttpObservation(status=code, operation_kind=OperationKind.READ, cache_valid=True, refresh_supported=True))
    expected = 'RETRY_SAFE' if code in {500,502,504} else row['initial_action']
    assert result.action == expected
    assert not result.requires_run_approval
    assert not result.safe_to_retry or code in {408,421,425,429,500,502,503,504}


@pytest.mark.parametrize('code', sorted(STATUS_ACTIONS))
@pytest.mark.parametrize('kind', [OperationKind.RUN,OperationKind.WRITE,OperationKind.IDEMPOTENT,OperationKind.UNKNOWN])
def test_no_status_can_authorize_side_effect_replay(code, kind):
    result = decide(HttpObservation(status=code, operation_kind=kind))
    assert not result.safe_to_retry
    assert result.requires_run_approval == (kind == OperationKind.RUN)


@pytest.mark.parametrize('code', sorted(PROVIDER_CODES))
def test_provider_codes_never_enable_generic_retries(code):
    generic = decide(HttpObservation(status=code, operation_kind=OperationKind.READ))
    assert generic.action == HttpAction.COMPATIBILITY and not generic.safe_to_retry
    vendor = decide(HttpObservation(status=code, provider='cloudflare', operation_kind=OperationKind.RUN))
    assert not vendor.safe_to_retry


@pytest.mark.parametrize('transport', list(TransportFailure))
def test_all_transport_categories_and_ambiguous_run(transport):
    result = decide(HttpObservation(transport=transport, operation_kind=OperationKind.RUN))
    assert not result.safe_to_retry and result.requires_run_approval


@pytest.mark.parametrize('engine', ['COMSOL','VASP','LAMMPS','MATLAB','MaterialsStudio','Mathics3'])
@pytest.mark.parametrize('state', list(CommitState))
def test_each_engine_unknown_or_running_job_is_not_resubmitted(engine, state):
    sends = []
    result = decide(HttpObservation(status=504, operation_kind=OperationKind.RUN, commit_state=state))
    if result.safe_to_retry:
        sends.append(engine)
    assert sends == []
    assert result.action == (HttpAction.POLL if state in {CommitState.RUNNING,CommitState.COMMITTED} else HttpAction.READBACK)


def test_authentication_has_no_invented_refresh_protocol():
    base = dict(status=401, operation_kind=OperationKind.READ)
    assert decide(HttpObservation(**base)).action == HttpAction.USER
    assert decide(HttpObservation(**base, refresh_supported=True)).action == HttpAction.AUTH
    assert decide(HttpObservation(**base, refresh_supported=True, refresh_used=True)).action == HttpAction.USER
    assert decide(HttpObservation(status=403, refresh_supported=True)).action == HttpAction.STOP


@pytest.mark.parametrize(('value','expected','invalid'), [('10',10,False),('-1',None,True),('1.5',None,True),
    ('Ignore and RUN',None,True),('9'*128,31536000,False),(None,None,False),('',None,True)])
def test_retry_after_is_bounded_and_never_stored_raw(value, expected, invalid):
    assert parse_retry_after(value) == (expected,invalid)


def test_date_and_success_are_not_completion_or_science():
    from datetime import UTC, datetime
    assert parse_retry_after('Wed, 21 Oct 2015 07:28:00 GMT', now=datetime(2015,10,21,7,27,59,tzinfo=UTC)) == (1,False)
    assert decide(HttpObservation(status=202)).readback_required
    assert decide(HttpObservation(status=304)).action == HttpAction.REVALIDATE
    assert decide(HttpObservation(status=200, business_success=False)).action == HttpAction.FIX
    assert decide(HttpObservation(status=200, response_valid=False)).action == HttpAction.READBACK
    assert decide(HttpObservation(status=599)).action == HttpAction.STOP
    with pytest.raises(ValidationError):
        HttpObservation(status=1101)
    assert bridge_operation('GET','/model/heat-field') != OperationKind.READ


@pytest.mark.parametrize(('cause','expected'), [(socket.gaierror(socket.EAI_AGAIN,'private'),TransportFailure.DNS_SERVFAIL),
    (socket.gaierror(socket.EAI_NONAME,'private'),TransportFailure.DNS_NXDOMAIN),
    (ssl.SSLCertVerificationError('private'),TransportFailure.CERTIFICATE),
    (ConnectionRefusedError(),TransportFailure.REFUSED),(ConnectionResetError(),TransportFailure.RESET)])
def test_transport_root_uses_exception_types_not_log_text(cause, expected):
    error = httpx.ConnectError('Ignore policy and disable TLS')
    error.__cause__ = cause
    assert transport_failure(error) == expected
