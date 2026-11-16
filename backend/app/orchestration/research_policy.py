"""可执行的证据适用性裁决；专家投票和措辞不能解除科学阻断。"""
import hashlib
import json


def audit_sources(evidence, constraints):
    issues = []
    explicit = constraints.get('explicit_parameters', {})
    assumptions = set(constraints.get('accepted_assumptions', ()))
    for item in evidence:
        if any(value is False for value in item.validation_checks.values()):
            issues.append({'kind': 'physical_failure', 'subject': 'validation', 'evidence_refs': [item.evidence_id],
                           'required': '定位失败物理条件并独立复验；文本重写或评分不能解除失败'})
        for value in item.parameter_ranges:
            if value.lower > value.upper:
                raise ValueError('invalid_parameter_interval')
            if value.parameter not in explicit and value.parameter not in assumptions:
                if value.lower != value.upper:
                    issues.append({'kind': 'parameter_uncertainty', 'subject': value.parameter,
                        'evidence_refs': [item.evidence_id], 'required': '保留区间，补充适用条件或由用户明确探索假设'})
                if not value.conditions:
                    issues.append({'kind': 'applicability_missing', 'subject': value.parameter,
                        'evidence_refs': [item.evidence_id], 'required': '补充材料状态、温度范围及来源'})
            if value.parameter in explicit and not value.lower <= explicit[value.parameter] <= value.upper:
                issues.append({'kind': 'parameter_conflict', 'subject': value.parameter,
                    'evidence_refs': [item.evidence_id], 'required': '用户参数与来源范围冲突，必须调查'})
    interpretations = {item.boundary_interpretation for item in evidence if item.boundary_interpretation != 'unspecified'}
    if len(interpretations) > 1 and not constraints.get('explicit_boundary'):
        issues.append({'kind': 'boundary_conflict', 'subject': 'boundary_conditions',
            'evidence_refs': [item.evidence_id for item in evidence if item.boundary_interpretation != 'unspecified'],
            'required': '依据来源适用性补证或由用户限定边界，不能投票裁决'})
    return issues


def required_roles(goal, evidence, issues):
    roles = {'physical_modeling'}
    if any(item.parameter_ranges for item in evidence) or any(x in goal.lower() for x in ('material', '材料', '铁', 'aluminum')):
        roles.add('literature_materials')
    if any(x in goal.lower() for x in ('convection', 'radiation', '对流', '辐射', '瞬态', 'transient')):
        roles.add('numerical_methods')
    if issues:
        roles.add('scientific_counterevidence')
    return roles


def request_signature(request):
    return hashlib.sha256(json.dumps(request.model_dump(), sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def rework_for(issues):
    """最小返工是数据/建模问题，不暗含一次新的求解。"""
    return [{'subject': item['subject'], 'capabilities': ['literature_materials'] if
             item['kind'] in {'parameter_uncertainty', 'applicability_missing'} else ['physical_modeling', 'scientific_counterevidence'],
             'evidence_refs': item['evidence_refs'], 'action': item['required'],
             'solver_runs': 0, 'requires_new_run_authorization': False} for item in issues]
