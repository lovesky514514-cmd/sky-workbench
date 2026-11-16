"""合成冷却数据验证审核机制；不代表真实 COMSOL 求解成功。"""
from copy import deepcopy
from dataclasses import replace
import hashlib
import io
import csv

import numpy as np
import pytest

from app.comsol.cooling_worker import lumped_reference, SIGMA
from app.execution.cooling_review import review_cooling
from app.execution.expert_contracts import ExpertScope
from app.orchestration.evidence import EvidenceVault
from app.scientific.cooling import cooling_plan
from app.scientific.schemas import ScientificPlan
from app.validators.execution_verdict import ExecutionVerdict
from app.validators.physics.cooling_audit import FLUX, KEYS, MODEL_SOURCE, FIELD_SOURCE, HISTORY_SOURCE, audit_cooling
from test_research_controller import setup


@pytest.fixture
def cooling():
    plan=ScientificPlan.model_validate(cooling_plan('三维铁块冷却5 mm x 5 mm x 25 mm，初温600 °C，环境20 °C，'
        '热导率45 W/(m*K)，密度7870 kg/m^3，比热650 J/(kg*K)，h=15 W/(m^2*K)，发射率0.65，持续10 s、每1 s输出'))
    p={k:float(v.value) for k,v in plan.parameters.items()}
    times=np.linspace(0,10,201);temperature=np.asarray(lumped_reference(p,times))
    area=2*(.005*.005+.005*.025+.005*.025)
    convection=area*15*(temperature-293.15)
    radiation=area*.65*SIGMA*(temperature**4-293.15**4)
    field=dict(zip(KEYS,[v.tolist() for v in (times,temperature,temperature,temperature,convection+radiation,convection,radiation)]))
    rows=[dict(zip(KEYS,row)) for row in zip(*(field[k] for k in KEYS))]
    records=[dict(solve_id=str(i),elements=i+1,dof=(i+1)*10 if i<3 else 30,
        auto_mesh_size=(6,5,4,4)[i],rtol=.00001 if i<3 else .000001,maximum_step_s=1. if i<3 else .5) for i in range(4)]
    histories={'profiles':[{'rows':deepcopy(rows)} for _ in range(4)],'records':records}
    model=dict(geometry_unit='m',size_m=[.005,.005,.025],boundary_count=6,flux_faces=[1,2,3,4,5,6],physics_type='HeatTransfer',
        flux_expression=FLUX,initial_expression='initial_temperature',solid_expressions={'k':'thermal_conductivity','rho':'density','Cp':'heat_capacity'},
        solid_modes={k:'userdef' for k in ('k','rho','Cp')},parameters=p,study_type='Transient',relative_tolerance=.000001,maximum_step_s=.5,dof=30)
    return plan,model,field,histories


def test_independent_numerical_recalculation_passes(cooling):
    result=audit_cooling(*cooling)
    assert len(result['checks'])==15
    assert all(v['status']=='PASS' for v in result['checks'].values()),result
    assert result['metrics']['internal_solve_count']==4


@pytest.mark.parametrize('damage', ['energy','temperature','time','mesh','duplicate_solve','wrong_boundary','parameter','saved_field','nan','missing'])
def test_false_worker_success_cannot_hide_raw_failures(cooling,damage):
    plan,model,field,histories=cooling
    if damage=='energy':
        for item in histories['profiles']:
            for row in item['rows']: row['outward_heat_W']*=2
    elif damage=='temperature': histories['profiles'][-1]['rows'][-1]['average_K']=900
    elif damage=='time': histories['profiles'][-1]['rows'][-1]['time_s']=1
    elif damage=='mesh': histories['profiles'][1]['rows'][-1]['maximum_K']+=100
    elif damage=='duplicate_solve': histories['records'][1]['solve_id']='0'
    elif damage=='wrong_boundary': model['flux_faces']=[1,2,3,4,5,5]
    elif damage=='parameter': model['parameters']['initial_temperature']=700
    elif damage=='saved_field': field['average_K'][-1]+=10
    elif damage=='nan': histories['profiles'][0]['rows'][0]['average_K']=float('nan')
    else: histories['profiles'].pop()
    assert any(v['status']=='FAIL' for v in audit_cooling(plan,model,field,histories)['checks'].values())


def test_review_reuses_rubric_and_rejects_old_attempt(tmp_path,cooling):
    plan,model,field,histories=cooling
    scope=ExpertScope(project_id='p',session_id='s',case_id='c',plan_id=plan.plan_id,plan_revision=1,run_id='r',attempt_id='a')
    vault=EvidenceVault(tmp_path,scope)
    artifacts=[vault.freeze(name,source,data) for name,source,data in (
        ('model',MODEL_SOURCE,model),('field',FIELD_SOURCE,field),('histories',HISTORY_SOURCE,histories))]
    verdict=ExecutionVerdict('r',plan.workflow_id,True,True,True,(),True)
    result=review_cooling(scope,plan,artifacts,verdict)
    assert result.status=='PASS' and result.score_total==100
    # 即使执行器声称全通过，物理反证仍优先于评分。
    model['flux_faces']=[1,2,3]
    artifacts[0]=vault.freeze('model',MODEL_SOURCE,model)
    result=review_cooling(scope,plan,artifacts,verdict)
    assert result.status=='BLOCKED' and result.revision['solver_runs']==0
    old=scope.model_copy(update={'attempt_id':'old'})
    artifacts[0]=replace(artifacts[0],scope=old)
    assert review_cooling(scope,plan,artifacts,verdict).status!='PASS'


def test_history_integrity_scope_and_bounded_reads(tmp_path,cooling):
    from app.orchestration.cooling_evidence import read_histories
    _,_,_,histories=cooling
    manifest={}
    for i,(profile,record) in enumerate(zip(histories['profiles'],histories['records']),1):
        stream=io.StringIO();writer=csv.DictWriter(stream,KEYS);writer.writeheader();writer.writerows(profile['rows'])
        data=stream.getvalue().encode();path=tmp_path/f'{i}.csv';path.write_bytes(data)
        record['history_sha256']=hashlib.sha256(data).hexdigest()
        manifest['convergence_'+str(i)]={'path':str(path),'sha256':record['history_sha256']}
    execution={'evidence_contract':{'artifacts':manifest},'validation':{'meshes':histories['records']}}
    assert len(read_histories(execution,tmp_path)['profiles'])==4
    (tmp_path/'1.csv').write_text('changed')
    with pytest.raises(ValueError,match='integrity'):read_histories(execution,tmp_path)


def test_frozen_cli_exposes_both_independent_readers():
    import subprocess
    import sys
    from pathlib import Path
    source=Path(__file__).resolve().parents[1]
    result=subprocess.run([sys.executable,str(source/'desktop_runtime.py'),'--help'],capture_output=True,text=True)
    assert result.returncode==0
    assert 'heat_readback_worker' in result.stdout and 'cooling_readback_worker' in result.stdout


@pytest.mark.asyncio
async def test_product_controller_dispatches_independent_cooling_review(setup,cooling,tmp_path,monkeypatch):
    import app.agent.runtime as env
    from app.models import AgentMode,AgentState
    from app.validators.base_validator import ValidationReport
    from test_v110_verdict import payload_for
    plan,model,field,histories=cooling
    plan.plan_id=setup.plan.plan_id;plan.revision=2
    plan.runnable=True;plan.planning_status='READY';plan.missing_information=[];plan.completion=None
    setup.store.commit_plan_revision(setup.sid,plan.model_dump(mode='json'),1)
    setup.store.set_state(setup.sid,AgentState.WAITING_FOR_EXECUTION)
    payload,report=payload_for(plan.workflow_id,tmp_path)
    class Validator:
        def precheck(self,_):return ValidationReport.from_checks('precheck',[])
        def validate_execution(self,*_):return report
    monkeypatch.setattr(env.validator_registry,'get',lambda _:Validator())
    calls=[]
    async def execute(name,args,on_event,retries):
        calls.append(name)
        if name=='comsol.read_saved_cooling':
            return {'model_sha256':hashlib.sha256(open(payload['artifacts']['model'],'rb').read()).hexdigest(),
                'run_id':args['run_id'],'attempt_id':args['attempt_id'],
                'solver_calls':0,'saved_model_unchanged':True,'model_readback':model,'field_readback':field}
        assert name=='comsol.heat_transfer_3d_cooling' and retries==0
        folder=tmp_path/'models'/'attempts'/args['run_id']/args['attempt_id']/'outputs'
        folder.mkdir(parents=True)
        for i,(profile,record) in enumerate(zip(histories['profiles'],histories['records']),1):
            path=folder/f'convergence_{i}.csv'
            with path.open('w',newline='') as stream:
                writer=csv.DictWriter(stream,KEYS);writer.writeheader();writer.writerows(profile['rows'])
            record['history_file']=path.name;record['history_sha256']=hashlib.sha256(path.read_bytes()).hexdigest()
            payload['artifacts']['convergence_'+str(i)]=str(path)
        payload['validation']['meshes']=histories['records']
        payload.update(run_id=args['run_id'],attempt_id=args['attempt_id'],tool=name,duration_ms=0,performance={})
        await on_event({'phase':'Solve'})
        await on_event({'phase':'Solution Validation'})
        return deepcopy(payload)
    monkeypatch.setattr(setup.runtime.tools,'execute',execute)
    await setup.runtime.handle_message(setup.sid,AgentMode.RUN,'Explicit synthetic test RUN')
    await setup.runtime._tasks[setup.sid]
    session=setup.store.get(setup.sid)
    assert session['state']=='COMPLETED',session['events'][-4:]
    assert calls==['comsol.heat_transfer_3d_cooling','comsol.read_saved_cooling']
    journal=setup.store.latest_run(setup.sid)
    review=json_read(journal['artifacts']['expert_review'])
    assert review['status']=='PASS' and review['score_total']==100
    assert len(journal['attempts'])==1
    assert setup.store.completed_evidence(journal['run_id'])['artifacts']['expert_review']['sha256']


def json_read(path):
    import json
    from pathlib import Path
    return json.loads(Path(path).read_text(encoding='utf-8'))
