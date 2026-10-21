import asyncio

from app.agent.intent_compiler import compile_intent
from app.agent.planner import ScientificPlanner


class NoProvider:
    async def complete(self, *args, **kwargs):
        return None


def test_planner_uses_original_for_deterministic_values_but_canonical_for_model_input():
    original = "建立二维稳态热传导模型。矩形 10 mm × 5 mm，左侧 373 K，右侧 293 K，上下绝热。材料：铝。输出温度场。"
    canonical = compile_intent(original).canonical_query
    payload, provider, _ = asyncio.run(ScientificPlanner(providers=NoProvider()).plan(canonical, [], original_prompt=original))
    assert provider == "offline-scientific-planner"
    assert payload["runnable"] is True
    assert payload["parameters"]["left_temperature"]["value"] == 373
    assert payload["original_request"] == original
