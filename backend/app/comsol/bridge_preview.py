"""从既有运行账本定位当前任务的只读预览模型。"""
from pathlib import Path

from app.validators.execution_verdict import verify_artifact_integrity


def completed_model(store, session_id: str, project_id: str | None, workspace: Path) -> Path:
    session = store.get(session_id)
    if not project_id or session['project_id'] != project_id:
        raise KeyError(session_id)
    run = store.latest_run(session_id)
    if not run or run['outcome'] != 'Completed' or run['cancel_requested']:
        raise ValueError('Current task has no completed model for read-only preview')
    evidence = store.completed_evidence(run['run_id']) or {}
    if evidence.get('run_id') != run['run_id'] or evidence.get('attempt_id') != run['attempt_id']:
        raise ValueError('Current model evidence does not match the completed attempt')
    path = run['artifacts'].get('model')
    record = evidence.get('artifacts', {}).get('model')
    if not isinstance(path, str) or not isinstance(record, dict):
        raise ValueError('Current task has no frozen model evidence')
    selected = Path(path).resolve()
    # 不递归猜测最新文件，旧尝试、其他任务及目录外的文件均不能被选中。
    root = (workspace / 'models' / 'attempts' / run['run_id'] / run['attempt_id']).resolve()
    selected.relative_to(root)
    if selected.suffix.lower() != '.mph' or record.get('path') != path:
        raise ValueError('Current model path does not match the frozen manifest')
    if not verify_artifact_integrity(selected, record)['passed']:
        raise ValueError('Current model integrity check failed; inspect diagnostic records')
    return selected
