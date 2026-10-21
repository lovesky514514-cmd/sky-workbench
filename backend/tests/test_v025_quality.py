import asyncio

import pytest

from app.agent.context_builder import ContextBuilder
from app.agent.planner import PlanGenerationFailure, ScientificPlanner, extract_heat_transfer_plan
from app.agent.scientific_tools import scientific_tool_layer
from app.providers.manager import CompletionResult
from app.scientific.schemas import PlanQualityStatus, ScientificPlan
from app.validators.plan_quality_validator import plan_quality_validator


BENCHMARK_A = "建立二维稳态热传导模型。矩形 10 mm × 5 mm，左侧 373 K，右侧 293 K，上下绝热。材料：铝。输出温度场。"
BENCHMARK_B = "建立铝板二维热传导模型，一边加热一边冷却。"
BENCHMARK_C = "建立二维稳态热传导模型，宽 1 cm × 高 5 mm，左侧 100 °C，右侧 20 °C，上下绝热，材料铝，输出温度场。"
BENCHMARK_D = "研究一个薄铝板左右温差情况下的稳态温度分布，上下不散热。"


def make_plan(prompt: str) -> ScientificPlan:
    payload = extract_heat_transfer_plan(prompt)
    assert payload is not None
    return ScientificPlan.model_validate(payload)


def test_benchmark_a_is_complete_with_explicit_material_warnings() -> None:
    plan = make_plan(BENCHMARK_A)
    report = plan_quality_validator.apply(plan)
    assert report.status == PlanQualityStatus.VALID_WITH_WARNINGS
    assert plan.runnable is True
    assert plan.parameters["width"].value == 10
    assert plan.parameters["height"].value == 5
    assert any("insulation" in item.lower() for item in plan.boundary_conditions)
    assert any("temperature" in item.lower() for item in plan.expected_outputs)


def test_benchmark_b_and_d_report_missing_information_without_defaults() -> None:
    for prompt in (BENCHMARK_B, BENCHMARK_D):
        plan = make_plan(prompt)
        report = plan_quality_validator.apply(plan)
        assert report.status == PlanQualityStatus.INVALID
        assert plan.runnable is False
        assert "width" not in plan.parameters
        assert "left_temperature" not in plan.parameters
        assert plan.missing_information


def test_benchmark_c_normalizes_mixed_length_and_celsius_units() -> None:
    plan = make_plan(BENCHMARK_C)
    assert plan.parameters["width"].value == pytest.approx(0.01)
    assert plan.parameters["height"].value == pytest.approx(0.005)
    assert plan.parameters["width"].unit == "m"
    assert plan.parameters["width"].original_value == 1
    assert plan.parameters["width"].original_unit == "cm"
    assert plan.parameters["width"].normalized_value == pytest.approx(0.01)
    assert plan.parameters["width"].normalized_unit == "m"
    assert plan.parameters["left_temperature"].value == pytest.approx(373.15)
    assert plan.parameters["right_temperature"].value == pytest.approx(293.15)
    assert plan.parameters["left_temperature"].original_unit == "°C"
    assert plan.parameters["left_temperature"].normalized_value == pytest.approx(373.15)
    assert plan.parameters["left_temperature"].normalized_unit == "K"


def test_english_width_and_height_phrase_is_recognized() -> None:
    plan = make_plan(
        "Create a 2D stationary heat transfer model of an aluminum rectangle "
        "10 mm wide and 5 mm high, with the left boundary at 373 K, the right "
        "boundary at 293 K, and thermal insulation on the top and bottom."
    )
    report = plan_quality_validator.apply(plan)
    assert report.status == PlanQualityStatus.VALID_WITH_WARNINGS
    assert plan.parameters["width"].original_value == 10
    assert plan.parameters["height"].original_value == 5
    assert plan.parameters["left_temperature"].value == 373
    assert plan.parameters["right_temperature"].value == 293


def test_provider_invalid_plan_gets_one_repair_then_visible_deterministic_envelope() -> None:
    class InvalidProvider:
        def __init__(self) -> None:
            self.calls = 0

        async def complete(self, *_args, **_kwargs):
            self.calls += 1
            return CompletionResult("not json", "ollama", "Ollama", "qwen3:8b")

    provider = InvalidProvider()
    planner = ScientificPlanner(provider)  # type: ignore[arg-type]
    payload, provider_name, model = asyncio.run(planner.plan(BENCHMARK_A, []))
    plan = ScientificPlan.model_validate(payload)
    assert provider_name == 'deterministic-safety-envelope' and model == 'none'
    assert plan.runnable and plan.boundary_conditions and plan.expected_outputs
    assert any('Provider output was rejected' in warning for warning in plan.warnings)
    assert provider.calls == 2


def test_context_is_bounded_and_exposes_only_high_level_tools() -> None:
    builder = ContextBuilder(max_input_tokens=500, recent_messages=3)
    bundle = builder.build(
        session={"messages": [{"role": "user", "content": "x" * 8_000}], "plan": None, "model": None},
        mode="ASK",
        prompt="What is thermal insulation?",
        citations=[],
        system_prompt="Cautious scientific assistant",
    )
    assert bundle.estimated_input_tokens <= 550
    assert bundle.truncated is True
    assert len(scientific_tool_layer.catalog()) == 12
    assert all("mcp" not in tool["name"].lower() for tool in scientific_tool_layer.catalog())
