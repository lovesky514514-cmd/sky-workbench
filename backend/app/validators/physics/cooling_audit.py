"""由宿主重算冷却硬门槛；独立 MPH 观测与四份原始 CSV 分别核对。"""
import math
import numpy as np

MODEL_SOURCE = 'cooling.model_readback.v1'
FIELD_SOURCE = 'cooling.field_readback.v1'
HISTORY_SOURCE = 'cooling.convergence_histories.v1'
KEYS = ('time_s','average_K','minimum_K','maximum_K','outward_heat_W','convection_W','radiation_W')
FLUX = 'heat_transfer_coefficient*(ambient_temperature-T)+emissivity*sigma_const*(ambient_temperature^4-T^4)'


def reference_temperature(p, times):
    """独立 Heun 积分，与求解工作进程的 RK4 算法分开实现。"""
    w,h,l = (p[k] for k in ('width','height','length'))
    factor=2*(w*h+w*l+h*l)/(p['density']*p['heat_capacity']*w*h*l)
    ambient=p['ambient_temperature']
    alpha=p.get('heat_capacity_temperature_coefficient',0.)
    tref=p.get('property_reference_temperature',ambient)
    def rate(temp):
        return -factor*(p['heat_transfer_coefficient']*(temp-ambient)+p['emissivity']*5.670374419e-8*(temp**4-ambient**4))/(1+alpha*(temp-tref))
    result=[]; t=0.; temp=p['initial_temperature']
    for target in times:
        while t < target-1e-12:
            step=min(.01,target-t)
            first=rate(temp)
            temp += step*(first+rate(temp+step*first))/2
            t += step
        result.append(temp)
    return np.asarray(result)


def audit_cooling(plan, model, field, histories):
    checks={}; metrics={}
    def record(name, passed): checks[name]={'status':'PASS' if bool(passed) else 'FAIL'}
    try:
        p={k:float(v.value) for k,v in plan.parameters.items()}
        ti,ta=p['initial_temperature'],p['ambient_temperature']; scale=ti-ta
        dimensions=[p[k] for k in ('width','height','length')]
        volume=math.prod(dimensions);w,h,l=dimensions;area=2*(w*h+w*l+h*l)
        expected={'k':'thermal_conductivity','rho':'density','Cp':'heat_capacity'}
        if plan.cooling_model.property_law == 'linear_temperature':
            expected['k']+='*(1+thermal_conductivity_temperature_coefficient*(T-property_reference_temperature))'
            expected['Cp']+='*(1+heat_capacity_temperature_coefficient*(T-property_reference_temperature))'
        record('model_geometry',model['geometry_unit']=='m' and np.allclose(model['size_m'],dimensions,rtol=1e-10,atol=0))
        record('model_physics',model['physics_type']=='HeatTransfer' and model['solid_expressions']==expected
            and all(model['solid_modes'][k]=='userdef' for k in expected)
            and model['initial_expression']=='initial_temperature' and model['study_type']=='Transient')
        record('boundary_selection',model['boundary_count']==6 and set(model['flux_faces'])==set(range(1,7)) and len(model['flux_faces'])==6 and model['flux_expression']==FLUX)
        record('parameter_readback',all(math.isclose(model['parameters'][k],v,rel_tol=1e-10,abs_tol=1e-12) for k,v in p.items()))
        profiles=[]
        for item in histories['profiles']:
            a=np.asarray([[row[key] for key in KEYS] for row in item['rows']],dtype=float)
            if a.ndim!=2 or len(a)<2 or len(a)>5000 or a.shape[1]!=7 or not np.isfinite(a).all():
                raise ValueError('invalid_history_shape_or_values')
            profiles.append(a)
        if len(profiles)!=4 or any(a.shape!=profiles[0].shape for a in profiles):
            raise ValueError('four_matching_histories_required')
        t=profiles[-1][:,0]
        if not np.all(np.diff(t)>0) or not np.allclose(t,profiles[0][:,0],rtol=0,atol=1e-9):
            raise ValueError('invalid_history_time')
        record('independent_saved_profile',all(np.asarray(field[key]).shape==(len(t),) and
            np.allclose(field[key],profiles[-1][:,i],rtol=1e-8,atol=1e-7) for i,key in enumerate(KEYS)))
        a=profiles[-1];mean,low,high=a[:,1],a[:,2],a[:,3]
        record('finite_fields',all(np.isfinite(field[key]).all() for key in KEYS))
        record('temperature_bounds',np.all(ta-.01<=low) and np.all(low<=mean+1e-7) and np.all(mean<=high+1e-7) and np.all(high<=ti+.01))
        record('monotonic_cooling',np.all(np.diff(mean)<=.01) and mean[-1]<ti and abs(mean[0]-ti)<.01)
        requested=np.append(np.arange(0,p['duration'],p['output_interval']),p['duration'])
        record('requested_time',abs(t[0])<1e-9 and abs(t[-1]-p['duration'])<1e-9 and
            all(np.any(np.isclose(t,value,rtol=0,atol=1e-9)) for value in requested) and
            all(np.allclose(t,a[:,0],rtol=0,atol=1e-9) for a in profiles))
        alpha=p.get('heat_capacity_temperature_coefficient',0); tref=p.get('property_reference_temperature',ta)
        enthalpy=lambda temp:temp+alpha*(temp-tref)**2/2
        stored=p['density']*p['heat_capacity']*volume*(enthalpy(ti)-enthalpy(mean[-1]))
        energy=float(np.trapezoid(a[:,4],t)); components=np.trapezoid(a[:,5:7],t,axis=0)
        error=abs(energy-stored)/max(abs(stored),1e-30)
        record('energy_balance',stored>0 and error<=.02)
        record('energy_components',np.all(a[:,4:]>=-1e-10) and np.allclose(a[:,4],a[:,5]+a[:,6],rtol=1e-6,atol=1e-9) and abs(energy-sum(components))<=max(abs(energy),1e-30)*1e-6)
        hr=p['emissivity']*5.670374419e-8*(ti+ta)*(ti*ti+ta*ta)
        mink=p['thermal_conductivity']*min(1+p.get('thermal_conductivity_temperature_coefficient',0)*(v-tref) for v in (ti,ta))
        biot=(p['heat_transfer_coefficient']+hr)*volume/area/mink
        referr=float(np.max(np.abs(mean-reference_temperature(p,t)))/scale)
        record('small_biot_reference',0<=biot<.1 and referr<=.02)
        mesherr=float(np.max(np.abs(profiles[1][:,1:4]-profiles[2][:,1:4]))/scale)
        timeerr=float(np.max(np.abs(profiles[2][:,1:4]-profiles[3][:,1:4]))/scale)
        records=histories['records']
        record('independent_resolves',len(records)==4 and len({r['solve_id'] for r in records})==4 and
            all(r['elements']>0 for r in records) and records[0]['dof']<records[1]['dof']<records[2]['dof']==records[3]['dof'] and
            model['dof']==records[3]['dof'] and [r['auto_mesh_size'] for r in records]==[*plan.cooling_model.mesh_levels,plan.cooling_model.mesh_levels[-1]])
        record('mesh_agreement',mesherr<=.01)
        record('time_agreement',timeerr<=.002 and records[3]['rtol']<records[2]['rtol'] and records[3]['maximum_step_s']<records[2]['maximum_step_s'] and
            math.isclose(model['relative_tolerance'],records[3]['rtol'],rel_tol=1e-10) and math.isclose(model['maximum_step_s'],records[3]['maximum_step_s'],rel_tol=1e-10) and
            math.isclose(model['relative_tolerance'],plan.cooling_model.relative_tolerance/10,rel_tol=1e-10) and
            math.isclose(model['maximum_step_s'],p['output_interval']/2,rel_tol=1e-10))
        metrics=dict(energy_relative_error=error,reference_relative_error=referr,mesh_relative_difference=mesherr,
            time_relative_difference=timeerr,biot_upper_estimate=biot,raw_sample_count=len(t),internal_solve_count=len(records))
    except (KeyError,ValueError,TypeError,IndexError,OverflowError,ZeroDivisionError):
        record('complete_raw_evidence',False)
    return {'checks':checks,'metrics':metrics,'scope':'Reviewed PDE numerical consistency; material calibration and full-field gradients unverified'}
