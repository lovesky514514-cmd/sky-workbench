"""Build a reviewable, multi-engine modeling brief before selecting an executor."""

from dataclasses import asdict
import re

from app.scientific.domain_catalog import (
    CONCEPTS,
    DOMAIN_ENGINES,
    MATERIALS,
    OBJECT_WORDS,
)
from app.scientific.quantity import (
    find_quantity,
    geometry_quantities,
    parse_quantity,
    TOKEN,
    UNIT,
)
from app.scientific.request_schema import (
    EngineCandidate,
    PlanningOption,
    PlanningQuestion,
    RequestBrief,
    RequestFact,
)
from app.scientific.object_scope import bind_objects

QUANTITIES = {
    "initial_temperature": (r"初始温度|初温|initial temperature|T0\s*=", "temperature"),
    "ambient_temperature": (
        r"环境温度|环境|ambient temperature|ambient|Tamb\s*=",
        "temperature",
    ),
    "left_temperature": (r"左侧|左边界|\bleft\b", "temperature"),
    "right_temperature": (r"右侧|右边界|\bright\b", "temperature"),
    "thermal_conductivity": (
        r"热导率|导热系数|thermal conductivity|\bk\s*=",
        "thermal_conductivity",
    ),
    "density": (r"密度|density|\brho\s*=", "density"),
    "heat_capacity": (r"比热容|比热|heat capacity|\bCp\s*=", "heat_capacity"),
    "duration": (
        r"时长|计算时间|(?:冷却|计算)(?=\s*[\d.eE+-]+\s*(?:s\b|秒|分钟))|观察|duration|end time",
        "time",
    ),
    "output_interval": (
        r"输出间隔|output interval|每(?=\s*[\d.]+\s*(?:s|秒|分钟)\s*输出)",
        "time",
    ),
    "emissivity": (r"辐射率|发射率|emissivity|\bepsilon\s*=", "dimensionless"),
    "mean_concentration": (r"平均浓度|mean concentration", "concentration"),
    "concentration_amplitude": (r"振幅|amplitude", "concentration"),
    "heat_transfer_coefficient": (
        r"换热系数|heat transfer coefficient|\bh\s*=",
        "heat_transfer_coefficient",
    ),
    "diffusion_coefficient": (r"扩散系数|diffusion coefficient|\bD\s*=", "diffusion"),
    "left_concentration": (r"左侧|左边界|\bleft\b", "concentration"),
    "right_concentration": (r"右侧|右边界|\bright\b", "concentration"),
    "left_potential": (r"左侧|左边界|\bleft\b", "potential"),
    "right_potential": (r"右侧|右边界|\bright\b", "potential"),
}


def positive_mentions(text: str, words: tuple[str, ...]) -> tuple[list[str], list[str]]:
    """Keep negated processes out of inferred model terms; retain them as constraints."""
    hits = []
    excluded = []
    for word in words:
        pattern = (
            r"(?<![A-Za-z])" if word[0].isascii() and word[0].isalpha() else ""
        ) + re.escape(word)
        for match in re.finditer(pattern, text, re.I):
            before = re.split(
                r"[,，。;；\n]|\bbut\b", text[: match.start()], flags=re.I
            )[-1][-24:]
            if re.search(
                r"(?:不考虑|不使用|不用|不要(?:用)?|不是|不做|禁止|无需|不含|忽略|排除|无|without\s*|not\s+|no\s+|zero\s+)[\w\s]{0,6}$",
                before,
                re.I,
            ) or re.search(
                r"(?:without|no|zero)\s+(?:[A-Za-z-]+(?:\s*[,/]\s*|\s+(?:and|or|nor)\s+))+$",
                text[max(0, match.start() - 120) : match.start()],
                re.I,
            ):
                excluded.append(match.group())
            else:
                hits.append(match.group())
    return hits, excluded


def question(
    key: str, label: str, choices: list[tuple[str, str, str]]
) -> PlanningQuestion:
    return PlanningQuestion(
        key=key,
        question=label,
        options=[PlanningOption(id=k, label=v, consequence=c) for k, v, c in choices],
    )


def analyze_request(text: str) -> RequestBrief:
    concepts = []
    exclusions = []
    for concept in CONCEPTS:
        hits, negative = positive_mentions(text, concept.words)
        if hits:
            concepts.append(concept)
        exclusions.extend(negative)
    keys = {c.key for c in concepts}
    _, negative = positive_mentions(
        text,
        (
            "辐射",
            "对流",
            "反应",
            "迁移",
            "radiation",
            "convection",
            "reaction",
            "migration",
        ),
    )
    exclusions.extend(negative)
    # An ion choosing a site in a solid is different from continuum concentration transport.
    if (
        re.search(r"离子|\bion\b", text, re.I)
        and re.search(r"位点|位置|嵌入|进到|insertion site", text, re.I)
        and not re.search(r"液体|溶液|liquid|solution", text, re.I)
    ):
        insertion = next(c for c in CONCEPTS if c.key == "ionic_insertion")
        if insertion not in concepts:
            concepts.append(insertion)
            keys.add(insertion.key)
    domains = []
    if keys & {"electronic", "structure", "adsorption", "ionic_insertion"}:
        domains.append("quantum")
    if keys & {"particles", "adsorption", "ionic_insertion"} or (
        "structure" in keys and "electronic" not in keys
    ):
        domains.append("atomistic")
    continuum = keys & {
        "thermal",
        "mechanics",
        "transport",
        "flow",
        "electrical",
        "magnetic",
        "battery",
        "oscillation",
    }
    if continuum and (
        not keys & {"particles", "electronic"} or keys & {"battery", "magnetic"}
    ):
        domains.append("continuum")
    if "mathematics" in keys or "oscillation" in keys:
        domains.append("mathematics")
    if not domains and continuum:
        domains.append("continuum")

    facts = []
    conflicts = []
    dimensions = geometry_quantities(text, 3) or geometry_quantities(text, 2)
    axes = (
        ("width", "height", "length") if len(dimensions) == 3 else ("width", "height")
    )
    for name, q in zip(axes, dimensions):
        facts.append(
            RequestFact(
                key=name,
                value=q.normalized_value if q.issue is None else q.original,
                unit=q.si_unit,
                original=q.original,
                span=q.original_span,
                normalized=q.issue is None,
                issue=q.issue,
                quantity=asdict(q),
            )
        )
    for key, (labels, kind) in QUANTITIES.items():
        boundary_concepts = {
            "temperature": "thermal",
            "concentration": "transport",
            "potential": "electrical",
        }
        if key.startswith(("left_", "right_")) and boundary_concepts[kind] not in keys:
            continue
        observations = []
        for label in re.finditer(labels, text, re.I):
            q = find_quantity(text[label.start() :], labels, kind, gap=12)
            if q:
                # A shared side label is not a temperature/concentration/potential role by itself.
                if (
                    key.startswith(("left_", "right_"))
                    and q.issue == "unsupported_unit"
                    and any(
                        parse_quantity(q.original, other).issue is None
                        for other in boundary_concepts
                        if other != kind
                    )
                ):
                    continue
                span = tuple(value + label.start() for value in q.original_span)
                prefix = re.split(r"[,，;；\n。]", text[: label.start()])[-1].strip()
                scope = (
                    prefix
                    if re.fullmatch(r"[\u4e00-\u9fffA-Za-z_]{1,6}", prefix)
                    else ""
                )
                observations.append((q, span, scope))
        if observations:
            for q, span, scope in observations:
                # Distinct named objects may have distinct initial states, not contradictory values.
                scoped_key = (
                    key + "@" + scope if scope and len(observations) > 1 else key
                )
                if not any(f.key == scoped_key and f.span == span for f in facts):
                    facts.append(
                        RequestFact(
                            key=scoped_key,
                            value=q.normalized_value if q.issue is None else q.original,
                            unit=q.si_unit,
                            original=q.original,
                            span=span,
                            normalized=q.issue is None,
                            issue=q.issue,
                            quantity={**asdict(q), "original_span": span},
                        )
                    )
            for scoped_key in {
                f.key for f in facts if f.key == key or f.key.startswith(key + "@")
            }:
                values = {
                    (f.value, f.unit)
                    for f in facts
                    if f.key == scoped_key and f.normalized
                }
                if len(values) > 1:
                    conflicts.append(f"{scoped_key}: conflicting explicit values")
    for word, name in MATERIALS.items():
        hits, _ = positive_mentions(text, (word,))
        if hits and not any(f.key == "material:" + name for f in facts):
            match = re.search(re.escape(word), text, re.I)
            facts.append(
                RequestFact(
                    key="material:" + name,
                    value=name,
                    original=match.group(),
                    span=match.span(),
                )
            )
    objects = [word for word in OBJECT_WORDS if re.search(re.escape(word), text, re.I)]
    for match in re.finditer(rf"({TOKEN})\s*({UNIT})", text):
        if not any(
            f.span[0] < match.end() and f.span[1] > match.start() for f in facts
        ):
            facts.append(
                RequestFact(
                    key=f"quantity@{match.start()}",
                    value=match.group(),
                    unit=match.group(2),
                    original=match.group(),
                    span=match.span(),
                )
            )
    object_description = (
        " / ".join(objects[:4]) if objects else "对象或引用内容尚未提供"
    )
    geometry_known = bool(dimensions) and all(q.issue is None for q in dimensions)
    known = {f.key for f in facts if f.normalized}
    questions = []
    if not domains or len(domains) > 1:
        questions.append(
            question(
                "model_scale",
                "这次希望在哪个尺度或工具层次建模？",
                [
                    *[
                        (
                            d,
                            " / ".join(name for name, _ in DOMAIN_ENGINES[d]),
                            "先建立对应尺度的模型方案；不自动宣称引擎可执行。",
                        )
                        for d in domains
                    ],
                    (
                        "delegate",
                        "让 AI 提出尺度与工具方案",
                        "依据问题和资料提出候选，保留适用条件供审核。",
                    ),
                ],
            )
        )
    if not geometry_known and any(
        d in domains for d in ("continuum", "quantum", "atomistic")
    ):
        questions.append(
            question(
                "model_input",
                "模型的几何或原子结构准备怎么获得？",
                [
                    (
                        "delegate",
                        "让 AI 提出探索模型",
                        "生成带单位、适用条件和来源标记的候选；不当作实验事实。",
                    ),
                    (
                        "provide",
                        "我提供尺寸、结构或文件",
                        "保留明确输入，后续建议不能覆盖这些事实。",
                    ),
                ],
            )
        )
    if "thermal" in keys and not any(
        token in text.lower()
        for token in (
            "左",
            "右",
            "绝热",
            "对流",
            "辐射",
            "ambient",
            "insulat",
            "convection",
        )
    ):
        questions.append(
            question(
                "thermal_environment",
                "热量从哪里进入或离开模型？",
                [
                    (
                        "air",
                        "空气对流与辐射",
                        "需审核环境、表面及换热近似；不是流体域求解。",
                    ),
                    (
                        "contact",
                        "接触介质或其他物体",
                        "需选择介质/接触导热模型；不能套用空气冷却。",
                    ),
                    (
                        "delegate",
                        "让 AI 提出热边界方案",
                        "先比较冷却、加热或保温等候选，再审核数值。",
                    ),
                ],
            )
        )
    if any(d in domains for d in ("quantum", "atomistic")):
        questions.append(
            question(
                "interaction_model",
                "计算方法和相互作用模型如何确定？",
                [
                    (
                        "sourced",
                        "从适用资料与本地合法文件中选取",
                        "核对元素、适用条件、许可及引擎版本。",
                    ),
                    (
                        "delegate",
                        "让 AI 检索并提出候选",
                        "候选保持未验证；不生成假的赝势或力场文件。",
                    ),
                ],
            )
        )
    if "mathematics" in domains and not re.search(
        r"[=∫]|\b(?:sin|cos|exp|sqrt)\s*\(", text, re.I
    ):
        questions.append(
            question(
                "mathematical_model",
                "公式或数据从哪里来？",
                [
                    (
                        "delegate",
                        "让 AI 提出符号模型",
                        "明确建模假设，未知系数先保留为符号。",
                    ),
                    ("provide", "我提供方程或数据", "忠实保留表达式、变量和数据来源。"),
                ],
            )
        )
    if not domains:
        questions.append(
            question(
                "research_goal",
                "目前缺少可确定的研究对象或目标，下一步如何继续？",
                [
                    (
                        "provide",
                        "补充对象、目标或引用内容",
                        "不编造“这个模型/这个方程”的具体内容。",
                    ),
                    (
                        "delegate",
                        "让 AI 列出可选建模方向",
                        "先澄清研究范围，再生成参数化草案。",
                    ),
                ],
            )
        )
    questions.append(
        question(
            "parameter_policy",
            "缺失参数采用哪种补全方式？",
            [
                (
                    "propose",
                    "让 AI 提出候选并逐项审核",
                    "允许推进草案，候选带单位、来源、理由和适用条件。",
                ),
                (
                    "symbolic",
                    "先保留符号参数",
                    "先形成方程和模型结构，数值确定后再求解。",
                ),
                ("provide", "我补充实际数据", "保留已有明确值，只补缺失字段。"),
            ],
        )
    )
    for conflict in conflicts:
        questions.insert(
            0,
            question(
                "conflict:" + conflict.split(":")[0],
                conflict,
                [("clarify", "我说明哪一个值有效", "冲突解决前不选择数值或执行。")],
            ),
        )
    engines = [
        EngineCandidate(engine=name, role=role)
        for d in domains
        for name, role in DOMAIN_ENGINES[d]
    ]
    engines = list({e.engine: e for e in engines}.values())
    equations = list(dict.fromkeys(c.equation for c in concepts))
    outputs = list(dict.fromkeys(c.output for c in concepts)) or [
        "待确定研究目标后生成相应结果"
    ]
    heating, _ = positive_mentions(
        text, ("加热", "升温", "只烧", "烧它的一头", "heat up", "heating")
    )
    cooling, _ = positive_mentions(text, ("冷却", "凉", "降温", "cooling", "cool down"))
    return bind_objects(
        text,
        RequestBrief(
            domains=domains,
            concepts=[c.key for c in concepts],
            object_description=object_description,
            facts=facts,
            exclusions=list(dict.fromkeys(exclusions)),
            conflicts=conflicts,
            equations=equations,
            requested_outputs=outputs,
            questions=questions,
            engine_candidates=engines,
            symbolic_inputs=symbolic_inputs(keys, known),
            next_steps=[
                "审核问题尺度与建模近似",
                "保留用户事实，补充候选参数或符号关系",
                "检查引擎/模块/资料能力",
                "审核后预检并运行已支持的适配器",
            ],
            model_outline={
                "object": object_description,
                "geometry_status": "explicit"
                if geometry_known
                else "needs_structure_choice",
                "parameter_status": "conflicting"
                if conflicts
                else "explicit_and_symbolic",
                "thermal_action": "heating"
                if heating
                else "cooling"
                if cooling
                else "undetermined",
                "initial_state": "hot_unspecified"
                if re.search(r"烧红|烧热|红热|red.hot|hot block", text, re.I)
                else "requires_review",
                "comparison_requested": bool(
                    re.search(r"比较|对比|哪个|两种|compare|comparison", text, re.I)
                ),
                "contact_requested": bool(
                    re.search(r"接触|贴在|粘在|contact|support", text, re.I)
                ),
                "phase_change_requested": bool(
                    re.search(r"熔化|熔融|相变|melt|phase change", text, re.I)
                ),
                "environment": "liquid"
                if re.search(
                    r"(?:放进|放到|浸|投入).{0,8}[水油]|水淬|quench|(?:in|into)\s+(?:water|oil)",
                    text,
                    re.I,
                )
                else "requires_review",
                "study_intent": "transient"
                if any(
                    w in text.lower()
                    for w in (
                        "多久",
                        "随时间",
                        "慢慢",
                        "变化",
                        "transient",
                        "time-dependent",
                    )
                )
                else "requires_review",
            },
        ),
    )


def symbolic_inputs(concepts: set[str], known: set[str]) -> list[dict[str, str]]:
    """Unknowns describe a model family; no coefficient is silently given a value."""
    families = {
        "thermal": [
            ("thermal_conductivity", "W/(m*K)", "导热率或其温度关系"),
            ("density", "kg/m^3", "密度或其温度关系"),
            ("heat_capacity", "J/(kg*K)", "比热容或其温度关系"),
            ("initial_temperature", "K", "初始温度场 T(x,0)"),
            ("heat_source", "W/m^3", "体热源 Q(x,t)，可审核为零"),
        ],
        "mechanics": [
            ("constitutive_law", "model", "材料本构关系 sigma(epsilon,...)"),
            ("load", "model", "载荷、约束、接触与初态"),
        ],
        "flow": [
            ("viscosity", "Pa*s", "流变关系与适用流动状态"),
            ("inlet", "model", "入口、出口与壁面条件"),
        ],
        "transport": [
            ("diffusion_coefficient", "m^2/s", "扩散系数或本构关系"),
            ("concentration", "mol/m^3", "初始和边界浓度/通量"),
            ("reaction", "model", "反应项 R，需要审核"),
        ],
        "electrical": [("current_law", "model", "导电率、电流与反应关系及电边界")],
        "magnetic": [
            ("electromagnetic_regime", "model", "静态、频域或时域与材料/激励")
        ],
        "battery": [("cell_model", "model", "化学体系、几何、荷电状态、动力学和工况")],
        "electronic": [
            ("electronic_method", "model", "电子结构近似、赝势与数值收敛设置")
        ],
        "structure": [("structure", "file", "元素、晶格、原子位置及稳定性判据")],
        "particles": [
            ("interaction_potential", "file", "适用势/力场及其合法文件"),
            ("ensemble", "model", "系综、边界和积分设置"),
        ],
        "adsorption": [
            ("surface_adsorbate", "model", "表面、吸附物、覆盖度及能量参照")
        ],
        "ionic_insertion": [("sites", "model", "宿主、离子及候选位点/迁移路径")],
        "oscillation": [
            ("dynamic_coefficients", "model", "质量、阻尼、刚度及初始/激励条件")
        ],
        "mathematics": [
            ("expression", "expression", "原始方程、变量、定义域或经审核的候选关系")
        ],
    }
    return [
        dict(symbol=name, unit=unit, purpose=purpose)
        for concept in sorted(concepts)
        for name, unit, purpose in families.get(concept, [])
        if name not in known
    ]
