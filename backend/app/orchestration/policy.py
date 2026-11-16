"""Heat 首个领域的证据驱动决策；只有总控可将建议变成任务图修改。"""
from dataclasses import dataclass


@dataclass(frozen=True)
class GraphDecision:
    additions: tuple[tuple[str, str, tuple[str, ...]], ...] = ()
    cancellations: tuple[str, ...] = ()
    reason: str = ''
    evidence_refs: tuple[str, ...] = ()
    finish: bool = False


def next_decision(checkpoint, findings) -> GraphDecision:
    verified = set(checkpoint.verified)
    known_tasks = {rule.task.task_id for rule in checkpoint.rules}
    if 'execute-reviewed-plan' not in known_tasks:
        return GraphDecision((('execute-reviewed-plan', 'execution', ('inspect-approved-plan',)),),
            reason='Approved plan inspection admits one bounded execution', evidence_refs=('plan-inspection',))
    if 'identify-evidence-gaps' not in known_tasks:
        return GraphDecision((('identify-evidence-gaps', 'review', ('execute-reviewed-plan',)),),
            reason='A worker receipt requires independent evidence assessment', evidence_refs=('worker-execution',))
    gaps = findings.get('identify-evidence-gaps', {}).get('missing', ())
    if gaps and 'acquire-independent-readback' not in known_tasks:
        return GraphDecision((('acquire-independent-readback', 'acquisition', ('identify-evidence-gaps',)),),
            reason='Independent physical evidence is missing; acquire without another solve', evidence_refs=('evidence-gaps',))
    if gaps and 'challenge-model' not in known_tasks:
        return GraphDecision((('challenge-model', 'challenge', ('acquire-independent-readback',)),
                              ('challenge-numerics', 'challenge', ('acquire-independent-readback',)),
                              ('mesh-refinement-assessment', 'challenge', ('challenge-numerics',))),
            reason='Admitted model and field require separate counterevidence checks', evidence_refs=('heat-model', 'heat-field'))
    if 'mesh-refinement-assessment' in checkpoint.pending:
        checks = findings.get('challenge-numerics', {})
        if checks and all(value['status'] == 'PASS' for value in checks.values()):
            return GraphDecision(cancellations=('mesh-refinement-assessment',),
                reason='Analytical field checks pass; mesh refinement has no value for this bounded benchmark',
                evidence_refs=('challenge-numerics-evidence',))
        return GraphDecision(reason='Unresolved numerical counterevidence requires the pending scoped investigation',
                             evidence_refs=('challenge-numerics-evidence',))
    if 'independent-review' not in known_tasks:
        dependencies = tuple(name for name in ('challenge-model', 'challenge-numerics', 'mesh-refinement-assessment') if name in verified)
        return GraphDecision((('independent-review', 'review', dependencies or ('identify-evidence-gaps',)),),
            reason='Independent review arbitrates preserved supporting and counterevidence', evidence_refs=('worker-execution',))
    return GraphDecision(finish=True)
