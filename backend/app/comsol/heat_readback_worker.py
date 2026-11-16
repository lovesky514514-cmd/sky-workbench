"""独立进程只读加载已登记 MPH，采集 N03 需要的模型和九点场证据。"""
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
    from jpype import JArray, JDouble

    args = json.loads(Path(request).read_text(encoding='utf-8'))
    path = Path(args['model_path']).resolve(strict=True)
    root = Path(args['workspace']).resolve()
    path.relative_to(root / 'models')
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    if before != args['model_sha256']:
        raise ValueError('saved_model_integrity_lost')
    client = mph.start(cores=1)
    model = client.load(str(path))
    try:
        component = model.java.component('comp1')
        geometry = component.geom('geom1')
        rectangle = geometry.feature('r1')
        unit = str(geometry.lengthUnit())
        scale = {'m': 1., 'cm': .01, 'mm': .001}[unit]
        width, height = [float(value) * scale for value in rectangle.getDoubleArray('size')]
        physics = component.physics('ht')
        solid = physics.feature('solid1')
        def literal(text, expected_unit):
            value, actual = str(text).split('[', 1)
            if actual.rstrip(']') != expected_unit:
                raise ValueError('unsupported_readback_unit')
            return float(value)
        k = literal(solid.getString('k'), 'W/(m*K)')
        left = physics.feature('temp_left')
        right = physics.feature('temp_right')
        edge_map = {side: [int(v) for v in component.selection('wb_' + side).entities()]
                    for side in ('left', 'right', 'top', 'bottom')}
        if [int(v) for v in left.selection().entities()] != edge_map['left'] or [int(v) for v in right.selection().entities()] != edge_map['right']:
            raise ValueError('temperature_selection_drift')
        # 非零热源/额外边界特征不被这个固定领域审核器冒充支持。
        expected = {'solid1': 'SolidHeatTransferModel', 'init1': 'init', 'ins1': 'ThermalInsulation',
                    'temp_left': 'TemperatureBoundary', 'temp_right': 'TemperatureBoundary'}
        for tag in physics.feature().tags():
            feature = physics.feature(tag)
            if feature.isActive() and (str(tag) not in expected or str(feature.getType()) != expected[str(tag)]):
                raise ValueError('unreviewed_active_heat_feature')
        if any(not physics.feature(tag).isActive() for tag in expected):
            raise ValueError('required_heat_feature_inactive')
        insulated = set(int(v) for v in physics.feature('ins1').selection().entities())
        if insulated != set(edge_map['top'] + edge_map['bottom']):
            raise ValueError('insulation_selection_drift')
        if str(solid.getString('k_mat')) != 'userdef':
            raise ValueError('conductivity_not_user_defined')
        model_readback = {'schema_version': 1, 'geometry_kind': 'rectangle', 'width_m': width,
            'height_m': height, 'conductivity_W_m_K': k, 'heat_source_W_m3': 0., 'boundaries': [
                {'side': 'left', 'kind': 'fixed_temperature', 'temperature_K': literal(left.getString('T0'), 'K')},
                {'side': 'right', 'kind': 'fixed_temperature', 'temperature_K': literal(right.getString('T0'), 'K')},
                {'side': 'top', 'kind': 'insulated'}, {'side': 'bottom', 'kind': 'insulated'}]}
        coordinates = [(width * x, height * y) for x in (0., .5, 1.) for y in (0., .5, 1.)]
        interp = model.java.result().numerical().create('sky4_audit_points', 'Interp')
        interp.set('expr', ['T', 'ht.dfluxx', 'ht.dfluxy'])
        interp.setInterpolationCoordinates(JArray(JDouble, 2)([
            [x / scale for x, _ in coordinates], [y / scale for _, y in coordinates]]))
        values = np.asarray(interp.getData())[:, 0, :]
        if values.shape != (3, 9) or not np.isfinite(values).all():
            raise ValueError('invalid_independent_field_shape')
        field = {'schema_version': 1, 'samples': [dict(x_m=x, y_m=y, temperature_K=float(values[0, i]),
            qx_W_m2=float(values[1, i]), qy_W_m2=float(values[2, i])) for i, (x, y) in enumerate(coordinates)]}
        if hashlib.sha256(path.read_bytes()).hexdigest() != before:
            raise ValueError('readback_modified_saved_model')
        payload = {'success': True, 'run_id': args['run_id'], 'attempt_id': args['attempt_id'],
            'model_readback': model_readback, 'field_readback': field, 'model_sha256': before,
            'comsol_version': str(client.version), 'solver_calls': 0, 'saved_model_unchanged': True}
        emit_result(payload)
        return 0
    finally:
        client.remove(model)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--request', required=True)
    sys.exit(main(parser.parse_args().request))
