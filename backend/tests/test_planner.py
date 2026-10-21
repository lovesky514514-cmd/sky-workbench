from app.agent.planner import extract_heat_transfer_plan


def test_chinese_heat_transfer_example_is_parsed_with_units_and_provenance():
    plan = extract_heat_transfer_plan(
        '建立二维稳态热传导模型。矩形 10 mm × 5 mm，左边界 373 K，右边界 293 K，其余边界绝热。'
    )
    assert plan is not None
    assert plan['workflow_id'] == 'heat_transfer_2d_stationary'
    assert plan['parameters']['width']['value'] == 10
    assert plan['parameters']['height']['unit'] == 'mm'
    assert plan['parameters']['left_temperature']['value'] == 373
    assert plan['parameters']['right_temperature']['value'] == 293
    assert all('source' in parameter for parameter in plan['parameters'].values())


def test_unsupported_problem_is_not_misclassified_as_heat_transfer():
    assert extract_heat_transfer_plan('建立三维固体力学悬臂梁并施加边界载荷。') is None

