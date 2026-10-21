"""Small scientific workflow vocabulary exposed to the agent and UI."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Literal


@dataclass(frozen=True, slots=True)
class ScientificTool:
    name: str
    purpose: str
    mode: tuple[str, ...]
    risk: Literal["read-only", "plan-write", "project-write"]
    mapped_to: tuple[str, ...]
    execution_policy: str


SCIENTIFIC_TOOLS = (
    ScientificTool("search_knowledge", "Find traceable project and literature evidence", ("ASK", "PLAN"), "read-only", ("knowledge.hybrid_search",), "May run automatically"),
    ScientificTool("read_source", "Read a selected citable source chunk", ("ASK", "PLAN"), "read-only", ("knowledge.chunk_store",), "May run automatically"),
    ScientificTool("inspect_environment", "Inspect COMSOL, Python, Java and provider readiness", ("ASK", "PLAN", "RUN"), "read-only", ("environment.detect", "providers.detect"), "May run automatically"),
    ScientificTool("list_project_files", "List bounded project inputs and prior artifacts", ("ASK", "PLAN"), "read-only", ("projects.tree",), "May run automatically"),
    ScientificTool("inspect_model", "Read the current normalized COMSOL model state", ("ASK", "PLAN", "RUN"), "read-only", ("session.model",), "May run automatically"),
    ScientificTool("create_plan", "Create a typed ScientificPlan without executing COMSOL", ("PLAN",), "plan-write", ("agent.scientific_planner",), "Requires PLAN mode"),
    ScientificTool("update_plan", "Persist an explicit user plan edit and revalidate it", ("PLAN",), "plan-write", ("session.plan",), "Requires explicit user edit"),
    ScientificTool("validate_plan", "Run plan quality and physics prechecks", ("PLAN", "RUN"), "read-only", ("validators.plan_quality", "validators.physics"), "Mandatory before RUN"),
    ScientificTool("build_model", "Build geometry, materials and physics from the saved plan", ("RUN",), "project-write", ("comsol.heat_transfer_2d:build",), "Only runtime may invoke after precheck"),
    ScientificTool("solve_model", "Solve the saved model study", ("RUN",), "project-write", ("comsol.heat_transfer_2d:solve",), "Only runtime may invoke after build validation"),
    ScientificTool("validate_result", "Check convergence, boundaries, ranges and reference error", ("RUN",), "read-only", ("validators.result",), "Mandatory before success"),
    ScientificTool("export_result", "Export MPH, figure, logs and Model Card", ("RUN",), "project-write", ("comsol.heat_transfer_2d:export", "agent.model_card"), "Only after validated solution"),
)


class ScientificToolLayer:
    def catalog(self) -> list[dict[str, object]]:
        return [asdict(tool) for tool in SCIENTIFIC_TOOLS]

    def relevant(self, mode: str, prompt: str = "") -> list[ScientificTool]:
        candidates = [tool for tool in SCIENTIFIC_TOOLS if mode in tool.mode]
        lower = prompt.lower()
        if mode == "ASK" and not any(term in lower for term in ("model", "模型", "comsol", "environment", "环境")):
            candidates = [tool for tool in candidates if tool.name not in {"inspect_model", "inspect_environment"}]
        return candidates

    def prompt_summary(self, mode: str, prompt: str = "") -> str:
        tools = self.relevant(mode, prompt)
        return "\n".join(f"- {tool.name}: {tool.purpose} [{tool.execution_policy}]" for tool in tools)


scientific_tool_layer = ScientificToolLayer()
