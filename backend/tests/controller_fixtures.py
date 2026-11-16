"""合成底座的只读返回数据；仅用于自动测试，不是真实 COMSOL 证据。"""
from hashlib import sha256
from pathlib import Path


def saved_heat_fixture(plan, model_path):
    def value(name):
        item = plan.parameters[name]
        return float(item.normalized_value if item.normalized_value is not None else item.value)
    def length(name):
        item = plan.parameters[name]
        return value(name) * {'m': 1., 'mm': .001, 'cm': .01}[item.normalized_unit or item.unit]
    w, h = length('width'), length('height')
    left, right, k = value('left_temperature'), value('right_temperature'), value('thermal_conductivity')
    return {'model_sha256': sha256(Path(model_path).read_bytes()).hexdigest(), 'solver_calls': 0,
        'saved_model_unchanged': True,
        'model_readback': {'schema_version': 1, 'geometry_kind': 'rectangle', 'width_m': w, 'height_m': h,
            'conductivity_W_m_K': k, 'heat_source_W_m3': 0., 'boundaries': [
                {'side': 'left', 'kind': 'fixed_temperature', 'temperature_K': left},
                {'side': 'right', 'kind': 'fixed_temperature', 'temperature_K': right},
                {'side': 'top', 'kind': 'insulated'}, {'side': 'bottom', 'kind': 'insulated'}]},
        'field_readback': {'schema_version': 1, 'samples': [
            dict(x_m=x*w, y_m=y*h, temperature_K=left+(right-left)*x,
                 qx_W_m2=k*(left-right)/w, qy_W_m2=0.)
            for x in (0., .5, 1.) for y in (0., .5, 1.)]}}
