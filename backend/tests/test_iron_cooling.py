import asyncio
import math

from fastapi.testclient import TestClient
import pytest

from app.agent.intent_compiler import compile_intent
from app.agent.planner import ScientificPlanner, extract_heat_transfer_plan
from app.agent.runtime import AgentRuntime
from app.agent.tool_registry import ToolRegistry
from app.scientific.cooling import cooling_plan, WORKFLOW
from app.scientific.plan_completion import accept_completion
from app.scientific.quantity import geometry_quantities
from app.scientific.schemas import ScientificPlan
from app.session.store import SessionStore
from app.validators.physics.cooling_validator import CoolingValidator
from app.comsol.cooling_worker import cooling_sample_times, lumped_reference, SIGMA

PROMPT = '我想做一个3x3x20mm的烧红的铁块，现在想分析热变化。'


class Offline:
    async def complete(self,*args,**kwargs):
        return None


@pytest.mark.parametrize('text', ['3x3x20mm','3 × 3 × 20 mm','3mm x 3mm x 20mm','0.3cm x 3mm x 2cm'])
def test_three_axes_keep_explicit_units(text):
    quantities = geometry_quantities(text,3)
    assert [q.normalized_value for q in quantities] == [.003,.003,.02]
    for q in quantities:
        assert q.issue is None
        assert text[q.original_span[0]:q.original_span[1]] == q.original


@pytest.mark.parametrize('text', ['3x3x-20mm','3x3x20','3x3x20parsecs','3x3x0mm'])
def test_invalid_sizes_do_not_select_the_cooling_executor(text):
    assert cooling_plan('烧红的铁块 '+text+' 冷却') is None


def test_original_request_produces_unapproved_transient_draft():
    plan,_,_ = asyncio.run(ScientificPlanner(Offline()).plan(PROMPT,[]))
    intent = compile_intent(PROMPT)
    assert intent.physics == ['Heat Transfer in Solids']
    assert intent.confidence == 'needs_confirmation'
    assert plan['workflow_id'] == WORKFLOW and not plan['runnable']
    assert set(plan['parameters']) == {'width','height','length'}
    assert not plan['completion']['parameters']['initial_temperature']['user_confirmed']
    assert extract_heat_transfer_plan('3x3x20mm iron heat transfer') is None


def test_review_acceptance_units_and_water_scope():
    draft = ScientificPlan.model_validate(cooling_plan(PROMPT))
    accepted = accept_completion(draft,draft.completion.proposal_id,{},mode='auto')
    assert CoolingValidator().precheck(accepted).passed
    assert accepted.parameters['initial_temperature'].value == 1073.15
    assert accepted.parameters['emissivity'].unit == '1'
    assert cooling_plan(PROMPT+'放到水里。') is None
    water, _, _ = asyncio.run(ScientificPlanner(Offline()).plan(PROMPT+'放到水里。', []))
    assert water['workflow_id'] is None and water['completion'] and not water['runnable']


def test_reference_matches_analytic_convection_limit():
    draft = ScientificPlan.model_validate(cooling_plan(PROMPT))
    accepted = accept_completion(draft,draft.completion.proposal_id,{},mode='auto')
    p = {k:float(v.value) for k,v in accepted.parameters.items()}
    p['emissivity'] = 0
    w,h,l = p['width'],p['height'],p['length']
    tau = p['density']*p['heat_capacity']*w*h*l/(p['heat_transfer_coefficient']*2*(w*h+w*l+h*l))
    expected = p['ambient_temperature']+(p['initial_temperature']-p['ambient_temperature'])*math.exp(-300/tau)
    assert abs(lumped_reference(p,[0,300])[-1]-expected)<1e-6


def test_sparse_requested_outputs_do_not_undermine_energy_integration():
    import numpy as np

    draft = ScientificPlan.model_validate(cooling_plan(PROMPT))
    accepted = accept_completion(draft,draft.completion.proposal_id,{},mode='auto')
    p = {k:float(v.value) for k,v in accepted.parameters.items()}
    p.update(output_interval=60, density=7870, heat_capacity=449)
    requested, audit = cooling_sample_times(p)
    assert list(requested) == [0,60,120,180,240,300]
    assert set(requested) <= set(audit)
    w,h,l = (p[k] for k in ('width','height','length'))
    capacity = p['density']*p['heat_capacity']*w*h*l
    area = 2*(w*h+w*l+h*l)

    def balance_error(times):
        temperatures = np.asarray(lumped_reference(p,times))
        ambient = p['ambient_temperature']
        heat_loss = area*(p['heat_transfer_coefficient']*(temperatures-ambient)
                         + p['emissivity']*SIGMA*(temperatures**4-ambient**4))
        stored_loss = capacity*(p['initial_temperature']-temperatures[-1])
        return abs(np.trapezoid(heat_loss,times)-stored_loss)/stored_loss

    # The former user-output quadrature misses the strong initial radiation.
    assert balance_error(requested) > .02
    assert balance_error(audit) < .002


def test_http_plan_and_scope_approval(tmp_path,monkeypatch):
    from app import main

    store = SessionStore(tmp_path/'session.sqlite');store.initialize()
    runtime = AgentRuntime(sessions=store,planner=ScientificPlanner(Offline()),tools=ToolRegistry())
    monkeypatch.setattr(runtime,'_retrieve',lambda *args:[])
    monkeypatch.setattr(main,'session_store',store)
    monkeypatch.setattr(main,'agent_runtime',runtime)
    monkeypatch.setenv('COMSOL_WORKBENCH_SESSION_TOKEN','fixture')
    client = TestClient(main.app,headers={'X-Workbench-Token':'fixture'})
    session = client.post('/api/sessions',json={}).json()
    url = '/api/sessions/'+session['id']
    response = client.post(url+'/messages',json={'mode':'PLAN','content':PROMPT})
    assert response.status_code==200
    plan = response.json()['plan'];proposal = plan['completion']
    assert plan['planning_status']=='DRAFT_NEEDS_INPUT'
    response = client.post(url+'/plan/completion',json={'proposal_id':proposal['proposal_id'],
        'reviewed_digest':proposal['reviewed_digest'],'expected_revision':plan['revision'],'mode':'auto','values':{}})
    assert response.status_code==200
    accepted = response.json()['plan']
    assert accepted['runnable'] and accepted['workflow_id']==WORKFLOW
    assert accepted['parameters']['initial_temperature']['authorization']=='scope'
