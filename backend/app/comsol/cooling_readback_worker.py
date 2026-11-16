"""独立只读进程重开冷却 MPH；只观测已存解，不 solve、save 或改动物理节点。"""
import argparse
import hashlib
import json
from pathlib import Path
import sys

if not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.execution.scientific_transfer import emit_result


def main(request):
    import mph
    import numpy as np
    args = json.loads(Path(request).read_text(encoding='utf-8'))
    root = Path(args['workspace']).resolve()
    path = Path(args['model_path']).resolve(strict=True)
    path.relative_to(root/'outputs')
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    if before != args['model_sha256']:
        raise ValueError('saved_model_integrity_lost')
    client = mph.start(cores=1)
    model = client.load(str(path))
    try:
        java = model.java
        component = java.component('comp1')
        geom = component.geom('geom1')
        heat = component.physics('ht')
        solid = heat.feature('solid1')
        flux = heat.feature('cooling')
        expected = {'solid1':'SolidHeatTransferModel','init1':'init','ins1':'ThermalInsulation','cooling':'HeatFluxBoundary'}
        for tag in heat.feature().tags():
            node = heat.feature(tag)
            if node.isActive() and (str(tag) not in expected or str(node.getType()) != expected[str(tag)]):
                raise ValueError('unreviewed_active_cooling_feature')
        params = {key:float(java.param().evaluate(key)) for key in args['plan']['parameters']}
        def read(expr):
            return np.real(np.asarray(model.evaluate(expr))).reshape(-1).tolist()
        profile = {name:read(expr) for name,expr in (
            ('time_s','aveop1(t)'),('average_K','aveop1(T)'),('minimum_K','minop1(T)'),('maximum_K','maxop1(T)'),
            ('outward_heat_W','intop1(-(heat_transfer_coefficient*(ambient_temperature-T)+emissivity*sigma_const*(ambient_temperature^4-T^4)))'),
            ('convection_W','intop1(heat_transfer_coefficient*(T-ambient_temperature))'),
            ('radiation_W','intop1(emissivity*sigma_const*(T^4-ambient_temperature^4))'))}
        observation = {'geometry_unit':str(geom.lengthUnit()),'size_m':[float(v) for v in geom.feature('blk1').getDoubleArray('size')],
            'boundary_count':int(geom.getNBoundaries()),'flux_faces':[int(v) for v in flux.selection().entities()],
            'physics_type':str(heat.getType()),'flux_expression':str(flux.getString('q0')),
            'initial_expression':str(heat.feature('init1').getString('Tinit')),
            'solid_expressions':{key:str(solid.getString(key)) for key in ('k','rho','Cp')},
            'solid_modes':{key:str(solid.getString(key+'_mat')) for key in ('k','rho','Cp')},
            'parameters':params,'study_type':str(java.study('std1').feature('time').getType()),
            'relative_tolerance':float(java.sol('sol1').feature('t1').getDouble('rtol')),
            'maximum_step_s':float(java.sol('sol1').feature('t1').getDouble('maxstepbdf')),
            'dof':int(java.sol('sol1').getSize()[0])}
        if hashlib.sha256(path.read_bytes()).hexdigest() != before:
            raise ValueError('readback_modified_saved_model')
        payload = dict(success=True,run_id=args['run_id'],attempt_id=args['attempt_id'],model_sha256=before,
            solver_calls=0,saved_model_unchanged=True,comsol_version=str(client.version),
            model_readback=observation,field_readback=profile)
        emit_result(payload)
        return 0
    finally:
        client.remove(model)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--request',required=True)
    sys.exit(main(parser.parse_args().request))
