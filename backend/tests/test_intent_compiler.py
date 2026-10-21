from app.agent.intent_compiler import compile_intent


def test_clear_chinese_heat_intent_is_normalized():
    intent = compile_intent("建立二维稳态热传导模型，矩形 10 mm × 5 mm，铝，左边界 373 K，右边界 293 K，其余绝热，输出温度场。")
    assert intent.language == "zh-CN"
    assert intent.physics == ["Heat Transfer in Solids"]
    assert intent.study == ["Stationary"]
    assert {item.location for item in intent.boundary_conditions} >= {"Left", "Right"}
    assert not intent.validation_issues


def test_clear_english_tds_intent_is_normalized():
    intent = compile_intent("Create a 2D stationary TDS model. Left concentration 1000 mol/m^3, right concentration 100 mol/m^3, top and bottom no-flux, D = 1e-9 m^2/s.")
    assert intent.language == "en-US"
    assert intent.physics == ["Transport of Diluted Species"]
    assert intent.parameters["diffusion_coefficient"].value == 1e-9
    assert not intent.unknowns


def test_vague_chinese_diffusion_keeps_values_unresolved():
    intent = compile_intent("左边浓一点右边淡一点，中间扩散，上下不要漏出去")
    assert intent.physics == ["Transport of Diluted Species"]
    assert "Diffusion coefficient D" in intent.unknowns
    assert "Left fixed concentration" in intent.unknowns
    assert intent.confidence == "missing_information"


def test_negative_diffusion_coefficient_is_recorded_but_rejected_by_intent_gate():
    intent = compile_intent("二维稳态 TDS，D = -1e-9 m^2/s")
    assert intent.parameters["diffusion_coefficient"].value == -1e-9
    assert intent.validation_issues == ["Diffusion coefficient D must be greater than zero"]


def test_canonical_query_changes_after_structured_user_edit():
    first = compile_intent("左边浓一点右边淡一点，中间扩散")
    edited = compile_intent("二维稳态 TDS，左边界 1000 mol/m^3，右边界 100 mol/m^3，D = 1e-9 m^2/s")
    assert first.canonical_query != edited.canonical_query
    assert edited.unknowns == []
