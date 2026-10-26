"""Keep explicit Chinese material properties attached to their source text."""

from __future__ import annotations

import unittest

from app.agent.intent_compiler import compile_intent
from app.agent.planner import extract_heat_transfer_plan
from app.scientific.schemas import ScientificPlan


class ChinesePropertyProvenanceTests(unittest.TestCase):
    def test_explicit_values_are_not_benchmark_assumptions(self) -> None:
        for conductivity_label, capacity_label in (
            ("导热系数", "比热"),
            ("热导率", "比热容"),
        ):
            with self.subTest(conductivity_label=conductivity_label):
                request = (
                    "建立二维稳态传热模型：铝板矩形 10 mm × 5 mm，"
                    "左边界温度 373 K，右边界 293 K，上下边界绝热；"
                    f"材料{conductivity_label} 241 W/(m*K)、密度 2710 kg/m^3、"
                    f"{capacity_label} 905 J/(kg*K)。求解温度场。"
                )
                intent = compile_intent(request)
                plan = ScientificPlan.model_validate(extract_heat_transfer_plan(request))

                for name, value in (
                    ("thermal_conductivity", 241),
                    ("density", 2710),
                    ("heat_capacity", 905),
                ):
                    self.assertEqual(intent.parameters[name].value, value)
                    self.assertEqual(plan.parameters[name].value, value)
                    self.assertEqual(plan.parameters[name].source, "user prompt")
                    self.assertFalse(plan.parameters[name].assumption)
                self.assertFalse(any("benchmark assumption" in item for item in plan.warnings))
