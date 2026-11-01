"""Common model outlines and fact preservation, independent of any solver template."""

from app.scientific.domain_catalog import CONCEPTS, DOMAIN_ENGINES
from app.scientific.request_analysis import analyze_request
from app.scientific.schemas import PlanCompletion, ScientificParameter, ScientificPlan
from app.scientific.request_schema import EngineCandidate, RequestBrief, RequestFact


def apply_model_choices(plan: ScientificPlan) -> ScientificPlan:
    """Apply reviewed structural choices without interpreting permission as missing data."""
    brief = plan.modeling_brief
    if brief is None:
        return plan
    scale = brief.answers.get("model_scale")
    if scale in DOMAIN_ENGINES:
        brief.domains = [scale]
        brief.engine_candidates = [
            EngineCandidate(engine=name, role=role)
            for name, role in DOMAIN_ENGINES[scale]
        ]
    environment = brief.answers.get("thermal_environment")
    if environment == "air" and any(
        word in brief.exclusions for word in ("对流", "辐射", "convection", "radiation")
    ):
        raise ValueError(
            "Air convection/radiation conflicts with an explicit exclusion"
        )
    if environment == "air":
        plan.boundary_conditions = [
            "候选空气对流/辐射边界：-n·k∇T = h(T-T_ambient) + epsilon*sigma_SB*(T^4-T_ambient^4)；待补表面/环境数据"
        ]
    elif environment == "contact":
        plan.boundary_conditions = [
            "接触传热：耦合接触两侧温度与热流；待补接触区域、介质及热阻关系"
        ]
    brief.model_outline["reviewed_choices"] = dict(brief.answers)
    if brief.answers.get("contact_model") == "ideal":
        for relation in brief.relations:
            if relation.kind == "contact":
                relation.condition = "perfect_contact_reviewed"
    return plan


def preserve_user_facts(plan: ScientificPlan, brief: RequestBrief) -> ScientificPlan:
    grouped: dict[str, list[RequestFact]] = {}
    for fact in brief.facts:
        if fact.normalized and fact.issue is None:
            grouped.setdefault(fact.key, []).append(fact)
    for key, facts in grouped.items():
        if len({(f.value, f.unit) for f in facts}) != 1:
            plan.parameters.pop(key, None)
            if plan.completion:
                plan.completion.parameters.pop(key, None)
            continue
        fact = facts[0]
        plan.parameters[key] = ScientificParameter(
            name=key,
            symbol=key,
            value=fact.value,
            unit=fact.unit.replace("²", "^2").replace("³", "^3"),
            source="user prompt",
            status="User Provided",
            verified=True,
            original_value=fact.quantity.get("original_value", fact.value),
            original_unit=fact.quantity.get("original_unit", fact.unit),
            normalized_value=fact.value,
            normalized_unit=fact.unit,
            quantity=fact.quantity
            or {
                "original": fact.original,
                "original_span": list(fact.span),
                "si_unit": fact.unit,
                "normalized_value": fact.value,
                "issue": None,
            },
        )
        if plan.completion:
            plan.completion.parameters.pop(key, None)
    if brief.conflicts:
        plan.missing_information = list(
            dict.fromkeys([*plan.missing_information, *brief.conflicts])
        )
    return plan


def build_general_plan(
    prompt: str, brief: RequestBrief | None = None
) -> ScientificPlan:
    brief = brief or analyze_request(prompt)
    concepts = [c for c in CONCEPTS if c.key in brief.concepts]
    explicit_shapes = [
        f
        for f in brief.facts
        if f.key in {"width", "height", "length"} and f.normalized
    ]
    geometry = (
        [" × ".join(f"{f.value:g} {f.unit}" for f in explicit_shapes)]
        if explicit_shapes
        else [f"待审核的模型对象：{brief.object_description}；结构来源见补全选择。"]
    )
    if brief.model_outline.get("geometry_status") == "scoped_entities":
        geometry = [
            entity.label
            + ": "
            + " × ".join(
                f"{f.value:g} {f.unit}"
                for f in brief.facts
                if f.entity_id == entity.id and f.role == "geometry" and f.normalized
            )
            for entity in brief.entities
            if any(
                f.entity_id == entity.id and f.role == "geometry" for f in brief.facts
            )
        ]
        geometry = geometry or ["各对象的几何与属性作用域待确认"]
    materials = list(
        dict.fromkeys(
            str(f.value) for f in brief.facts if f.key.startswith("material:")
        )
    )
    physics = list(dict.fromkeys(c.physics for c in concepts)) or [
        "待选择物理/数学模型；可由 AI 提出候选"
    ]
    plan = ScientificPlan(
        title="参数化科学模型草案",
        research_goal=prompt,
        original_request=prompt,
        physics=physics,
        geometry=geometry,
        materials=materials,
        boundary_conditions=["待审核边界、初值或原子/数学模型约束"],
        study=["待审核研究类型与尺度"],
        mesh_strategy=["依据选定模型和收敛要求提出离散方案"],
        validation=[
            "单位与事实一致性",
            "适用的解析/守恒/收敛检查；不从正常退出推断科学正确",
        ],
        expected_outputs=brief.requested_outputs,
        missing_information=[q.question for q in brief.questions if q.required],
        modeling_brief=brief,
        planning_status="DRAFT_NEEDS_INPUT",
        completion=PlanCompletion(
            source="example",
            questions=brief.questions,
            reason="先建立参数化模型方案；你可以委托 AI 补充、保留符号，或提供真实数据。",
            remaining_missing=[q.question for q in brief.questions if q.required],
            applicability="模型方案、用户批准、引擎可执行与科学验证分别记录。",
        ),
        steps=[{"phase": "Plan", "label": step} for step in brief.next_steps],
        warnings=["当前是模型结构草案；候选引擎尚未通过本次模型执行验收。"],
    )
    return preserve_user_facts(plan, brief)


def enrich_plan(plan: ScientificPlan, prompt: str) -> ScientificPlan:
    brief = analyze_request(prompt)
    # Geometry axis order belongs to the adapter contract, not to sentence order.
    if plan.workflow_id == "solid_mechanics_uniaxial_bar":
        axes = {"width": "length", "height": "width", "length": "height"}
        for fact in brief.facts:
            fact.key = axes.get(fact.key, fact.key)
    elif plan.workflow_id in {
        "tds_2d_stationary_diffusion",
        'tds_2d_transient_closed',
        "electrochem_primary_current",
    }:
        for fact in brief.facts:
            if fact.key == "width":
                fact.key = "length"
    # Server facts are reconstructed from the request, not trusted from provider JSON.
    if plan.modeling_brief:
        brief.answers = dict(plan.modeling_brief.answers)
    plan.modeling_brief = brief
    if not plan.expected_outputs:
        plan.expected_outputs = brief.requested_outputs
    return preserve_user_facts(plan, brief)


def merge_general_provider(
    plan: ScientificPlan, deterministic: ScientificPlan
) -> ScientificPlan:
    """Let semantic planning add model structure while keeping observed facts authoritative."""
    brief = (
        deterministic.modeling_brief
        or analyze_request(
            deterministic.original_request or deterministic.research_goal
        )
    ).model_copy(deep=True)
    if plan.modeling_brief:
        semantic = plan.modeling_brief
        # Unsupported vocabulary can be understood online without extending the offline catalog.
        brief.domains = list(dict.fromkeys([*brief.domains, *semantic.domains]))
        brief.equations = list(dict.fromkeys([*brief.equations, *semantic.equations]))
        brief.requested_outputs = list(
            dict.fromkeys([*brief.requested_outputs, *semantic.requested_outputs])
        )
        brief.symbolic_inputs = [*brief.symbolic_inputs, *semantic.symbolic_inputs]
        brief.questions = list(
            {q.key: q for q in [*brief.questions, *semantic.questions]}.values()
        )
        brief.questions = [q for q in brief.questions if q.key not in brief.answers]
        brief.inference_method = "provider_semantic"
    plan.modeling_brief = brief
    plan.workflow_id = None
    plan.runnable = False
    plan.original_request = deterministic.original_request
    plan.research_goal = deterministic.research_goal
    # Only the user transaction may write choices or service/execution status.
    brief.engine_candidates = list(
        {
            name: EngineCandidate(engine=name, role=role)
            for domain in brief.domains
            for name, role in DOMAIN_ENGINES.get(domain, ())
        }.values()
    )
    if not plan.physics:
        plan.physics = deterministic.physics
    if not plan.geometry:
        plan.geometry = deterministic.geometry
    if any(
        f.key in {"width", "height", "length"} and f.normalized for f in brief.facts
    ):
        plan.geometry = deterministic.geometry
    if deterministic.materials:
        plan.materials = deterministic.materials
    if not plan.expected_outputs:
        plan.expected_outputs = deterministic.expected_outputs
    from app.scientific.plan_completion import attach_completion

    plan = attach_completion(plan, plan.model_copy(deep=True))
    if plan.completion is None:
        plan.completion = PlanCompletion(
            source="ai", reason="审核语义规划给出的模型方案", questions=brief.questions
        )
    plan.completion.questions = brief.questions
    plan.missing_information = list(
        dict.fromkeys([*plan.missing_information, *brief.conflicts])
    )
    return apply_model_choices(preserve_user_facts(plan, brief))
