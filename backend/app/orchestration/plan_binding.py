"""只有总控可把待审核通用草案绑定到已有且符合目标的执行契约。"""
from app.scientific.cooling import WORKFLOW, cooling_plan
from app.scientific.request_analysis import analyze_request
from app.scientific.schemas import ScientificPlan


def bind_supported_draft(plan):
    if plan.runnable or plan.workflow_id not in {None, WORKFLOW}:
        return plan
    goal = plan.original_request or plan.research_goal
    candidate = cooling_plan(goal)
    if candidate is None:
        return plan
    from app.agent.planner import faithful_workflow
    if not faithful_workflow(candidate, analyze_request(goal), goal):
        return plan
    bound = ScientificPlan.model_validate(candidate)
    bound.plan_id, bound.revision = plan.plan_id, plan.revision
    bound.ai_provider, bound.ai_model = plan.ai_provider, plan.ai_model
    bound.sources = plan.sources
    bound.runnable = False
    bound.planning_status = 'DRAFT_NEEDS_INPUT'
    bound.missing_information = list(dict.fromkeys([*bound.missing_information, *plan.missing_information]))
    bound.warnings = list(dict.fromkeys([*bound.warnings, *plan.warnings]))
    bound.modeling_brief.model_outline['execution_contract_source'] = 'deterministic_existing_cooling_adapter'
    bound.modeling_brief.model_outline['scientific_status'] = 'NOT_RUN'
    return bound
