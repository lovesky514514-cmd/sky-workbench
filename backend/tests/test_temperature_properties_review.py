import pytest

from app.scientific.cooling import cooling_plan
from app.scientific.transient_diffusion import transient_diffusion_plan
from test_transient_diffusion import PROMPT
from app.agent.planner import ScientificPlanner
import asyncio
from test_general_modeling import Offline


def test_unsupplied_temperature_property_law_stays_a_draft():
    assert cooling_plan("3x3x20 mm 铁块烧红后冷却，考虑物性随温度变化") is None
    plan = cooling_plan(
        "3x3x20 mm 铁块烧红后冷却，线性温度相关物性，T_ref=298.15 K，alpha_k=-0.0003 1/K，alpha_Cp=0.0003 1/K"
    )
    assert plan["cooling_model"]["property_law"] == "linear_temperature"


@pytest.mark.parametrize(
    "extra",
    ["，初态 cos(pi*x/L)+sin(pi*x/L)", "，并考虑反应", "，左侧保持浓度 2 mol/m^3"],
)
def test_additional_physics_does_not_enter_a_closed_cosine_model(extra):
    request = (
        PROMPT.replace("初态 cos(pi*x/L)", "初态 cos(pi*x/L)+sin(pi*x/L)")
        if "初态" in extra
        else PROMPT + extra
    )
    assert transient_diffusion_plan(request) is None


@pytest.mark.parametrize(
    "extra",
    [
        "扩散系数随空间变化",
        "左侧浓度随时间变化",
        "采用分层材料",
        "扩散系数依赖浓度",
        "两种物质耦合扩散",
    ],
)
def test_nonconstant_or_coupled_transport_never_uses_the_stationary_scalar_adapter(
    extra,
):
    prompt = (
        "二维稳态纯溶质扩散 20 mm x 10 mm，扩散系数 1e-9 m^2/s，左侧 2 mol/m^3，右侧 0 mol/m^3，上下无通量，"
        + extra
    )
    plan, _, _ = asyncio.run(ScientificPlanner(Offline()).plan(prompt, []))
    assert plan["workflow_id"] is None and plan["completion"] and not plan["runnable"]
