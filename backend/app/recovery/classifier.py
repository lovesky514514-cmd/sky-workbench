"""规则按受信来源和反证分类。HTTP 状态仅是其中一个输入。"""
from __future__ import annotations

from app.recovery.schemas import Action, Cause, FailureEvent, Origin, RuleDiagnosis
from app.recovery.http_contracts import HttpAction, HttpObservation, OperationKind
from app.recovery.http_policy import decide

PLAN = '/api/sessions/{session_id}/plan'
COMPLETE = PLAN + '/completion'
PREVIEW = '/api/imported-mph/{item_id}/preview'
CONNECT = '/api/engine-tasks/connect'
RUN = '/model/study/run'

# 提示由宿主定义，错误正文不会作为建议进入提案。
RULES: dict[Cause, tuple[Action, str, str]] = {
    Cause.PLAN_REVISION: (Action.REFRESH, '请求与当前计划版本', '刷新当前计划并查看差异，重新审核后再提交。'),
    Cause.REVIEW_DIGEST: (Action.REFRESH, '当前 proposal、版本与审核摘要', '读取当前提案，提示用户复核差异。'),
    Cause.PLAN_PRECONDITION: (Action.DIAGNOSE, '计划及活跃运行状态', '先检查计划是否存在、是否可修订。'),
    Cause.PROJECT_REQUIRED: (Action.DIAGNOSE, '当前项目的可写状态', '检查所选项目，保持运行状态。'),
    Cause.OWNER_BUSY: (Action.DIAGNOSE, 'Bridge 所有者、模型身份及存活状态', '仅核查现有所有权，不抢占或释放。'),
    Cause.BRIDGE_START: (Action.DIAGNOSE, '连接器握手阶段和版本能力', '分步核对安装、编译和连接状态。'),
    Cause.HEAT_CORE: (Action.DIAGNOSE, '当前模型几何、网格、Study 和 Solver 读回', '对照已授权元数据并列出缺失对象。'),
    Cause.FIELD_ARRAY: (Action.DIAGNOSE, '已授权字段的形状、数据集身份和数值范围', '检查现有字段摘要，不补造或重新求值。'),
    Cause.MUTATION_READBACK: (Action.DIAGNOSE, '同一模型参数的当前读回', '核对模型身份和参数摘要，不重复写入。'),
    Cause.SOLVE_UNKNOWN: (Action.DIAGNOSE, '当前 RUN/attempt、Bridge 事件和独立提交证明', '核查是否仍在途或已提交，禁止自动重复 RUN。'),
    Cause.SOLVE_UNVERIFIED: (Action.DIAGNOSE, '同一尝试的求解与模型状态读回', '核对求解身份和新鲜读回，禁止自动重复 RUN。'),
    Cause.OUTPUT_EXISTS: (Action.DIAGNOSE, '现有产物的归属、哈希和完整性', '保留现有输出，禁止覆盖或删除。'),
    Cause.ARTIFACT_INVALID: (Action.DIAGNOSE, '原始产物及其摘要、生产尝试', '保留历史产物，核对当前证据。'),
    Cause.EVIDENCE_INVALID: (Action.DIAGNOSE, '失败的科学检查及当前尝试证据', '交给独立科学验证器复核。'),
    Cause.MODEL_TAG: (Action.DIAGNOSE, '当前模型标签和 Bridge 所有权', '核对当前模型的身份和绑定。'),
    Cause.BRIDGE_AUTH: (Action.FORBIDDEN, '当前 Bridge 认证状态', '检查授权配置，保持权限限制。'),
    Cause.RUN_APPROVAL: (Action.FORBIDDEN, '当前尝试的显式 RUN 审批', '保持阻断，交由原审批入口处理。'),
    Cause.TOOL_PERMISSION: (Action.FORBIDDEN, '专家任务能力和工具白名单', '拒绝越权调用。'),
    Cause.EVIDENCE_SCOPE: (Action.FORBIDDEN, '证据与项目、任务和尝试绑定', '拒收越权证据。'),
    Cause.EXPERT_STALE: (Action.FORBIDDEN, '当前专家与科学运行尝试', '保留历史，拒收过期结果。'),
    Cause.DEPENDENCY: (Action.FORBIDDEN, '已通过校验的前置结果', '等待或诊断前置任务，保持阻断。'),
    Cause.LICENSE: (Action.DIAGNOSE, '可信许可证状态', '检查许可是否可用，不更改许可。'),
    Cause.RESOURCE: (Action.DIAGNOSE, '内存、磁盘或运行资源状态', '核查资源与占用，不终止未知进程。'),
    Cause.ENV_MISSING: (Action.DIAGNOSE, '当前版本安装与依赖探针', '核对缺失依赖。'),
    Cause.ENV_UNSUPPORTED: (Action.DIAGNOSE, '系统能力和版本探针', '逐项核对支持范围。'),
    Cause.PROCESS_EXIT: (Action.DIAGNOSE, '拥有的进程身份与退出状态', '查看退出事件和当前尝试。'),
    Cause.DATABASE: (Action.DIAGNOSE, 'SQLite 事务与存储健康', '保留原始数据库和诊断历史。'),
    Cause.TIMEOUT: (Action.DIAGNOSE, '超时发生阶段及操作状态', '核查超时位置和在途操作。'),
    Cause.DISCONNECTED: (Action.DIAGNOSE, '连接中断阶段及操作状态', '核查网络和在途状态。'),
    Cause.SERVICE_UNAVAILABLE: (Action.DIAGNOSE, '服务健康及当前运行', '检查服务状态与等待条件。'),
    Cause.INTERNAL: (Action.DIAGNOSE, '异常来源及相关安全事件', '检查故障环节并采集受限快照。'),
    Cause.HTTP: (Action.DIAGNOSE, '操作契约、当前尝试和独立状态', '按 HTTP 策略核查状态，不自动重放写入或 RUN。'),
    Cause.UNKNOWN: (Action.DIAGNOSE, '模板路由、稳定错误码和当前状态', '补充可区分根因的只读证据。'),
}

TRANSPORT = {'read_timeout', 'connect_timeout', 'disconnected'}
_DIRECT = {
    Origin.EXPERT: {Cause.RUN_APPROVAL, Cause.TOOL_PERMISSION, Cause.EVIDENCE_SCOPE, Cause.EXPERT_STALE, Cause.DEPENDENCY},
    Origin.TOOL: {Cause.TOOL_PERMISSION, Cause.RUN_APPROVAL, Cause.PROCESS_EXIT, Cause.RESOURCE, Cause.EXPERT_STALE},
    Origin.SCIENCE: {Cause.EVIDENCE_INVALID, Cause.ARTIFACT_INVALID},
    Origin.ENVIRONMENT: {Cause.LICENSE, Cause.ENV_MISSING, Cause.ENV_UNSUPPORTED, Cause.RESOURCE, Cause.DATABASE},
    Origin.RUNTIME: {Cause.EXPERT_STALE, Cause.PROCESS_EXIT, Cause.RESOURCE, Cause.DATABASE},
    Origin.COMSOL: {Cause.LICENSE, Cause.RESOURCE, Cause.MODEL_TAG},
}


def classify(event: FailureEvent) -> RuleDiagnosis:
    f, code = event.facts, event.error_code
    cause = Cause.UNKNOWN
    negatives: tuple[str, ...] = ()
    if event.origin == Origin.API and event.http_status == 409:
        if (event.method, event.route_template) == ('PUT', PLAN):
            if code == 'plan_revision' or (f.expected_revision and f.observed_revision
                                            and f.expected_revision != f.observed_revision):
                cause = Cause.PLAN_REVISION
        elif (event.method, event.route_template) == ('POST', COMPLETE):
            if code == 'plan_revision' or (f.expected_revision and f.observed_revision
                                            and f.expected_revision != f.observed_revision):
                cause = Cause.PLAN_REVISION
            elif code == 'review_digest' or f.reviewed_digest_matches is False:
                cause = Cause.REVIEW_DIGEST
            elif code == 'plan_precondition':
                cause = Cause.PLAN_PRECONDITION
        elif (event.method, event.route_template) == ('POST', PREVIEW) and code == 'owner_busy':
            if f.owner_matches is not True:
                cause = Cause.OWNER_BUSY
            else:
                negatives = ('Bridge 当前所有者与目标匹配',)
        elif (event.method, event.route_template) == ('POST', CONNECT):
            if code == 'writable_project_required' or f.project_writable is False:
                cause = Cause.PROJECT_REQUIRED
            elif code == 'connector_handshake':
                cause = Cause.BRIDGE_START
    elif event.origin == Origin.BRIDGE:
        key = event.method, event.route_template
        if key == ('POST', RUN):
            if code in TRANSPORT and f.phase != 'before_submit':
                cause = Cause.SOLVE_UNKNOWN
            elif f.command_success is True and f.model_state_verified is False:
                cause = Cause.SOLVE_UNVERIFIED
            elif event.http_status in (409, 500) and f.phase != 'before_submit':
                cause = Cause.SOLVE_UNKNOWN
        elif event.http_status == 409:
            if key == ('GET', '/model/heat-core') and code == 'heat_core_incomplete':
                cause = Cause.HEAT_CORE
            elif key == ('GET', '/model/heat-field') and code == 'field_array' and f.field_shape_matches is not True:
                cause = Cause.FIELD_ARRAY
            elif key in {('POST', '/model/parameter'), ('DELETE', '/model/parameter')} and (
                f.command_success is True and f.model_state_verified is False
            ):
                cause = Cause.MUTATION_READBACK
            elif key == ('POST', '/model/heat-export') and code == 'export_exists':
                cause = Cause.OUTPUT_EXISTS
        if event.http_status in (401, 403) or code == 'bridge_auth':
            cause = Cause.BRIDGE_AUTH
    elif code in Cause._value2member_map_ and Cause(code) in _DIRECT.get(event.origin, set()):
        cause = Cause(code)

    if cause == Cause.UNKNOWN:
        if code in {'read_timeout', 'connect_timeout'}:
            cause = Cause.TIMEOUT
        elif code == 'disconnected':
            cause = Cause.DISCONNECTED
        elif event.http_status == 503:
            cause = Cause.SERVICE_UNAVAILABLE
        elif event.http_status == 500:
            cause = Cause.INTERNAL
    action, evidence, probe = RULES[cause]
    observation = f.http
    if observation is None and event.http_status is not None and event.http_status != 409:
        observation = HttpObservation(status=event.http_status)
    policy = decide(observation) if observation else None
    if cause == Cause.UNKNOWN and event.http_status != 409 and policy and policy.reason != 'unknown_status':
        cause = Cause.HTTP
        action, evidence, probe = RULES[cause]
    if policy:
        if policy.action == HttpAction.STOP and cause in {Cause.HTTP, Cause.DISCONNECTED, Cause.TIMEOUT}:
            action = Action.FORBIDDEN
        if cause == Cause.HTTP:
            probe = {
                HttpAction.COOLDOWN: '等待服务配额或冷却结束；当前任务可停止，禁止重复 RUN。',
                HttpAction.USER: '需要重新登录或核对服务权限；保留当前任务状态。',
                HttpAction.AUTH: '仅通过已有认证协议刷新一次；不重放 RUN。',
                HttpAction.READBACK: '任务可能已提交；只读核查现有作业，未知状态禁止补发 RUN。',
                HttpAction.FIX: '请求或数据格式不符合接口契约；提供修正建议，禁止盲目重试。',
                HttpAction.REVALIDATE: '重新核对当前版本和条件；不覆盖并发修改。',
                HttpAction.STOP: '停止自动恢复，核对证书、服务能力或明确权限。',
                HttpAction.RETRY: '仅已授权只读端点可在统一预算内重试。',
                HttpAction.RECONNECT: '只读核查连接健康；重连不代表重新启动求解。',
                HttpAction.COMPATIBILITY: '需要确认服务商协议；保留历史或扩展码，不自动执行。',
            }.get(policy.action, probe)
    return RuleDiagnosis(classification=cause, rule_id=f'recovery.{cause.value.lower()}',
                         action=action, required_evidence=(evidence,), counter_evidence=negatives,
                         safe_next_probe=probe, allow_retry=action == Action.REFRESH, http=policy)
