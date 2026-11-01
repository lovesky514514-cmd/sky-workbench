"""Reviewable suggestions stay separate from accepted execution inputs."""

from __future__ import annotations

import math
from dataclasses import asdict

from app.scientific.schemas import (
    ParameterStatus,
    PlanCompletion,
    ScientificParameter,
    ScientificPlan,
)
from app.scientific.quantity import parse_quantity
from app.scientific.static_contracts import HEAT_REQUIRED


DECISION_FIELDS = (
    "physics",
    "geometry",
    "materials",
    "boundary_conditions",
    "mesh_strategy",
    "study",
    "solver",
    "validation",
    "expected_outputs",
)


def proposed_parameter(
    parameter: ScientificParameter, reason: str
) -> ScientificParameter:
    item = parameter.model_copy(deep=True)
    item.assumption = True
    item.verified = False
    item.user_confirmed = False
    item.use_authorized = False
    item.authorization = "none"
    item.status = ParameterStatus.ASSUMPTION
    item.source = (
        "AI proposal; unverified"
        if not reason.startswith("Exploratory")
        else "Workbench exploratory example; unverified"
    )
    item.source_page = None
    item.source_chunk = None
    item.rationale = parameter.rationale or reason
    item.applicability = (
        parameter.applicability or "Confirm units and applicability before execution."
    )
    return item


def attach_completion(
    plan: ScientificPlan, candidate: ScientificPlan | None = None
) -> ScientificPlan:
    unapproved = {
        name: proposed_parameter(
            item, "An existing assumption still needs authorization."
        )
        for name, item in plan.parameters.items()
        if item.assumption and not (item.user_confirmed or item.use_authorized)
    }
    if not plan.missing_information and not unapproved:
        return plan
    for name in unapproved:
        plan.parameters.pop(name)
    reason = "AI suggests a candidate to complete the draft; it is not a measured or retrieved fact."
    if candidate is None and plan.workflow_id == "heat_transfer_2d_stationary":
        request = (plan.original_request or plan.research_goal).lower()
        # This example is deliberately narrow; never turn a transient/3D task into this benchmark.
        if any(word in request for word in ("铝", "aluminum", "aluminium")) and not any(
            word in request for word in ("瞬态", "暂态", "transient", "三维", "3d")
        ):
            from app.agent.planner import extract_heat_transfer_plan

            candidate = ScientificPlan.model_validate(
                extract_heat_transfer_plan(
                    "2D steady-state aluminum heat transfer 0.1 m x 0.05 m; "
                    "left 373.15 K; right 293.15 K; top and bottom thermal insulation"
                )
            )
            reason = "Exploratory aluminum plate example: 0.1 × 0.05 m, 373.15/293.15 K, 2D stationary, insulated sides."
    if candidate is None:
        if not unapproved:
            return plan
        candidate = plan.model_copy(deep=True)
        candidate.parameters = unapproved
    parameters = {
        name: proposed_parameter(value, reason)
        for name, value in candidate.parameters.items()
        if name not in plan.parameters
    }
    parameters.update(unapproved)
    decisions = {
        name: getattr(candidate, name)
        for name in DECISION_FIELDS
        if not getattr(plan, name) and getattr(candidate, name)
    }
    # Boundary roles may be partially specified. Preserve every explicit boundary.
    if plan.workflow_id == "heat_transfer_2d_stationary":
        boundaries = list(plan.boundary_conditions)
        for name, description in (
            ("left_temperature", "Left: prescribed left_temperature"),
            ("right_temperature", "Right: prescribed right_temperature"),
        ):
            if name in parameters:
                boundaries.append(description)
        if any("Top and bottom" in item for item in plan.missing_information):
            boundaries.append("Top and bottom: thermal insulation")
        if boundaries != plan.boundary_conditions:
            decisions["boundary_conditions"] = boundaries
    if parameters or decisions:
        resolved = set(parameters) | set(plan.parameters)
        remaining = list(candidate.missing_information)
        if plan.workflow_id == "heat_transfer_2d_stationary":
            # Missing labels are display text; derive readiness from typed fields instead.
            remaining = [
                f"Required parameter: {name}"
                for name in HEAT_REQUIRED
                if name not in resolved
            ]
            for name in (
                "physics",
                "geometry",
                "materials",
                "boundary_conditions",
                "study",
                "expected_outputs",
            ):
                if not decisions.get(name, getattr(plan, name)):
                    remaining.append(f"Required decision: {name}")
        plan.completion = PlanCompletion(
            source="example" if reason.startswith("Exploratory") else "ai",
            parameters=parameters,
            decisions=decisions,
            reason=reason,
            remaining_missing=remaining,
        )
    return plan


def accept_completion(
    plan: ScientificPlan,
    proposal_id: str,
    values: dict[str, float | str],
    *,
    mode: str = "review",
    selected_parameters: list[str] | None = None,
    decisions: dict[str, list[str]] | None = None,
    question_answers: dict[str, str] | None = None,
) -> ScientificPlan:
    proposal = plan.completion
    if proposal is None or proposal.proposal_id != proposal_id:
        raise ValueError("Proposal changed; reload and review the current draft")
    if set(values) - set(proposal.parameters):
        raise ValueError("Only proposed missing parameters can be filled here")
    selected = set(
        proposal.parameters if selected_parameters is None else selected_parameters
    )
    if selected - set(proposal.parameters) or set(values) - selected:
        raise ValueError("Only selected proposal parameters can be accepted")
    if mode not in {"manual", "review", "auto"}:
        raise ValueError("Unknown completion mode")
    if mode == "manual" and selected - set(values):
        raise ValueError("Fill each selected parameter in manual mode")
    if mode == "auto" and values:
        raise ValueError("Use review or manual mode to edit a suggestion")
    additional = decisions or {}
    allowed_decisions = set(proposal.decisions) | {
        field
        for field in DECISION_FIELDS
        if f"Required decision: {field}" in proposal.remaining_missing
    }
    if set(additional) - allowed_decisions or any(
        not any(v.strip() for v in vs) for vs in additional.values()
    ):
        raise ValueError("Supply a nonempty value for a listed missing decision")
    accepted = plan.model_copy(deep=True)
    answers = question_answers or {}
    questions = {q.key: q for q in proposal.questions}
    if set(answers) - set(questions):
        raise ValueError("Answer only questions in the reviewed proposal")
    for key, answer in answers.items():
        if answer not in {option.id for option in questions[key].options}:
            raise ValueError("Select a listed planning option")
    if accepted.modeling_brief:
        accepted.modeling_brief.answers.update(answers)
        accepted.modeling_brief.questions = [
            q for q in accepted.modeling_brief.questions if q.key not in answers
        ]
        accepted.modeling_brief.model_outline["reviewed_choices"] = dict(
            accepted.modeling_brief.answers
        )
    for name, item in proposal.parameters.items():
        if name not in selected:
            continue
        if name in plan.parameters:
            raise ValueError("A proposal cannot overwrite an existing input")
        value = item.model_copy(deep=True)
        if name in values:
            if isinstance(values[name], float) and not math.isfinite(values[name]):
                raise ValueError("A proposed value must be finite")
            if not str(values[name]).strip():
                raise ValueError("A proposed value cannot be empty")
            value.value = values[name]
            value.source = "User-selected assumption"
        # Confirmation authorizes use; it does not verify the physical value.
        value.user_confirmed = mode != "auto"
        value.use_authorized = True
        value.authorization = (
            "scope" if mode == "auto" else "manual" if name in values else "reviewed"
        )
        kind = quantity_kind(name)
        if kind is None:
            raise ValueError(
                f"Unsupported quantity type for {name}; define its unit contract before accepting it"
            )
        quantity = parse_quantity(f"{value.value} {value.unit}", kind)
        if quantity.issue:
            raise ValueError(f"Invalid quantity for {name}: {quantity.issue}")
        value.original_value, value.original_unit = (
            quantity.original_value,
            quantity.original_unit,
        )
        value.value = quantity.normalized_value
        value.unit = quantity.si_unit.replace("²", "^2").replace("³", "^3")
        value.quantity = asdict(quantity)
        value.normalized_value, value.normalized_unit = value.value, value.unit
        accepted.parameters[name] = value
    for name, value in {**proposal.decisions, **additional}.items():
        if name not in DECISION_FIELDS:
            raise ValueError(f"Unsupported completion field: {name}")
        setattr(accepted, name, list(value))
    accepted.missing_information = [
        label
        for label in proposal.remaining_missing
        if label not in {f"Required decision: {name}" for name in additional}
        and label not in {questions[key].question for key in answers}
    ]
    accepted.assumptions = list(
        dict.fromkeys([*accepted.assumptions, proposal.reason, proposal.applicability])
    )
    remaining_parameters = {
        name: item for name, item in proposal.parameters.items() if name not in selected
    }
    remaining_questions = [q for q in proposal.questions if q.key not in answers]
    accepted.completion = (
        PlanCompletion(
            source=proposal.source,
            parameters=remaining_parameters,
            reason=proposal.reason,
            applicability=proposal.applicability,
            remaining_missing=accepted.missing_information,
            questions=remaining_questions,
        )
        if remaining_parameters or accepted.missing_information or remaining_questions
        else None
    )
    if accepted.modeling_brief and answers:
        if answers.get("parameter_policy") == "symbolic":
            accepted.modeling_brief.model_outline["parameter_status"] = "symbolic"
        elif (
            answers.get("parameter_policy") == "propose"
            or "delegate" in answers.values()
        ):
            accepted.modeling_brief.next_steps.insert(
                0,
                "调用已配置语言模型/证据源提出参数候选，候选需再次审核；未连接时保留符号模型。",
            )
        if accepted.workflow_id is None:
            accepted.warnings = list(
                dict.fromkeys(
                    [
                        *accepted.warnings,
                        "模型结构已按选择更新；实际求解仍需适配器和相应输入，不能由选择操作自报成功。",
                    ]
                )
            )
    accepted.runnable = False
    accepted.revision += 1
    from app.scientific.general_planning import apply_model_choices

    return apply_model_choices(accepted)


def quantity_kind(name: str) -> str | None:
    name = name.split("@", 1)[0]
    if (
        name.startswith("delta_")
        and "temperature" in name
        or name.endswith("temperature_difference")
    ):
        return "temperature_difference"
    if name in {"width", "height", "length", "radius", "thickness"}:
        return "length"
    if name.endswith("_temperature") or name == "temperature":
        return "temperature"
    if name.endswith("_concentration"):
        return "concentration"
    return {
        "thermal_conductivity": "thermal_conductivity",
        "density": "density",
        "heat_capacity": "heat_capacity",
        "diffusion_coefficient": "diffusion",
        "heat_transfer_coefficient": "heat_transfer_coefficient",
        "emissivity": "dimensionless",
        "duration": "time",
        "output_interval": "time",
        "thermal_conductivity_temperature_coefficient": "inverse_temperature",
        "heat_capacity_temperature_coefficient": "inverse_temperature",
        "concentration_amplitude": "concentration",
    }.get(name)


def merge_provider_suggestions(plan: ScientificPlan, candidate: ScientificPlan) -> None:
    """Keep valid AI candidates for missing fields without granting any authority."""
    proposal = plan.completion
    if proposal is None:
        return
    suggested = dict(candidate.parameters)
    if candidate.completion:
        suggested.update(candidate.completion.parameters)
    used = False
    for name in proposal.parameters:
        item = suggested.get(name)
        if item is None or name in plan.parameters:
            continue
        kind = quantity_kind(name)
        if kind is None:
            continue
        quantity = parse_quantity(f"{item.value} {item.unit}", kind)
        if quantity.issue:
            continue
        value = proposed_parameter(item, "AI proposal; units and applicability require review.")
        value.value = quantity.normalized_value
        value.unit = quantity.si_unit.replace("²", "^2").replace("³", "^3")
        value.quantity = asdict(quantity)
        value.original_value, value.original_unit = quantity.original_value, quantity.original_unit
        value.normalized_value, value.normalized_unit = value.value, value.unit
        value.source = "AI proposal; unverified"
        value.source_page = value.source_chunk = None
        proposal.parameters[name] = value
        used = True
    if used:
        proposal.source = "ai"
        proposal.reason = "语言模型已提出缺失参数候选；请审核数值、单位和适用条件，候选不代表实测或科学验证。"


def sanitize_plan_edit(
    plan: ScientificPlan, previous: ScientificPlan | None
) -> list[str]:
    """Preserve server provenance only when the accepted value is unchanged."""
    rejected = []
    edited_assumptions = {}
    for name, item in plan.parameters.items():
        old = previous.parameters.get(name) if previous else None
        if old and item.value == old.value and item.unit == old.unit:
            plan.parameters[name] = old.model_copy(deep=True)
            continue
        if (
            item.user_confirmed
            or item.use_authorized
            or item.verified
            or item.source_chunk
            or item.source_page
        ):
            rejected.append(name)
        item.user_confirmed = item.use_authorized = item.verified = False
        item.authorization = "none"
        item.source_chunk = item.source_page = None
        item.original_value = item.original_unit = item.normalized_value = (
            item.normalized_unit
        ) = None
        item.quantity = {}
        item.source = "User-edited input; unverified"
        item.status = (
            ParameterStatus.ASSUMPTION
            if item.assumption
            else ParameterStatus.USER_PROVIDED
        )
        if item.assumption:
            edited_assumptions[name] = proposed_parameter(
                item, "An edited assumption requires renewed authorization."
            )
    # Neither an edited JSON proposal nor a client-supplied citation grants approval.
    plan.completion = (
        previous.completion.model_copy(deep=True)
        if previous and previous.completion
        else None
    )
    if edited_assumptions:
        for name in edited_assumptions:
            plan.parameters.pop(name)
        pending = plan.completion
        plan.completion = PlanCompletion(
            source=pending.source if pending else "ai",
            parameters={
                **(pending.parameters if pending else {}),
                **edited_assumptions,
            },
            decisions=pending.decisions if pending else {},
            reason="Edited assumptions require renewed review.",
            remaining_missing=pending.remaining_missing if pending else [],
            questions=pending.questions if pending else [],
        )
    if plan.completion:
        plan.completion.remaining_missing = [
            item
            for item in plan.completion.remaining_missing
            if not any(
                item == f"Required decision: {name}" and getattr(plan, name)
                for name in DECISION_FIELDS
            )
        ]
        if (
            not plan.completion.parameters
            and not plan.completion.decisions
            and not plan.completion.remaining_missing
            and not plan.completion.questions
        ):
            plan.completion = None
    plan.modeling_brief = (
        previous.modeling_brief.model_copy(deep=True)
        if previous and previous.modeling_brief
        else None
    )
    if previous and plan.original_request != previous.original_request:
        from app.scientific.request_analysis import analyze_request

        plan.modeling_brief = analyze_request(
            plan.original_request or plan.research_goal
        )
    plan.sources = previous.sources if previous else []
    if previous:
        plan.plan_id = previous.plan_id
        from app.scientific.execution_contract import scientific_inputs

        old_structure = scientific_inputs(previous.model_dump(mode="json"))
        new_structure = scientific_inputs(plan.model_dump(mode="json"))
        for structure in (old_structure, new_structure):
            structure.pop("parameters", None)
            structure.pop("missing_information", None)
        if old_structure != new_structure and previous.workflow_id:
            # An edit is not a compiler for new equations or boundary conditions.
            plan.workflow_id = None
            plan.warnings.append(
                "Model structure changed; generate and review an adapter-compatible plan before execution."
            )
        if scientific_inputs(plan.model_dump(mode="json")) != scientific_inputs(
            previous.model_dump(mode="json")
        ):
            # Approval is tied to the model, not just an unchanged numeric cell.
            renewed = {
                name: proposed_parameter(
                    item, "Execution inputs changed; review this assumption again."
                )
                for name, item in plan.parameters.items()
                if item.assumption
            }
            for name in renewed:
                plan.parameters.pop(name)
            pending = plan.completion
            plan.completion = PlanCompletion(
                source=pending.source if pending else "ai",
                parameters={**(pending.parameters if pending else {}), **renewed},
                decisions={
                    name: list(getattr(plan, name))
                    for name in DECISION_FIELDS
                    if getattr(plan, name)
                },
                reason="Execution inputs changed; prior approvals do not authorize the revised model.",
                remaining_missing=pending.remaining_missing if pending else [],
                questions=pending.questions if pending else [],
            )
            rejected.append("__execution_inputs_changed__")
    for material in plan.material_definitions:
        material.provenance = "user_provided"
        for item in material.properties.values():
            clear_generated_authority(item)
    return rejected


def clear_generated_authority(item: ScientificParameter) -> None:
    item.user_confirmed = item.use_authorized = item.verified = False
    item.authorization = "none"
    item.source_page = item.source_chunk = None
    item.status = ParameterStatus.ASSUMPTION
    item.assumption = True
    item.source = "Unverified proposed value"
