"""只读投影持久化检查点，供工作台查看当前和历史专家任务。"""

from app.execution.revision_checkpoint import RevisionCheckpoint


def project_workflow(checkpoint: RevisionCheckpoint) -> dict:
    tasks = []
    ready = set(checkpoint.ready())
    for rule in checkpoint.rules:
        task = rule.task
        published = checkpoint.verified.get(task.task_id)
        waiting = [dependency for dependency in task.dependencies if dependency not in checkpoint.verified]
        if published:
            state = "verified"
        elif checkpoint.status == "STOPPED":
            state = "stopped"
        elif task.task_id in checkpoint.in_flight:
            state = "running"
        elif task.task_id in ready:
            state = "ready"
        else:
            state = "waiting"
        tasks.append({
            "id": task.task_id, "capability": task.capability, "objective": task.objective,
            "dependencies": list(task.dependencies), "waiting_for": waiting, "state": state,
            "tools": list(task.requested_tools), "budget": task.budget_estimate.model_dump(),
            "inputs": list(task.input_evidence_refs),
            "outputs": [item.evidence_id for item in published.evidence] if published else [],
        })
    return {
        "scope": checkpoint.scope.model_dump(mode="json"), "status": checkpoint.status,
        "stop_reason": checkpoint.stop_reason, "limit": checkpoint.limit.model_dump(),
        "spent": checkpoint.spent.model_dump(), "tasks": tasks,
    }
