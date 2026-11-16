"""同工具/同上限的四组消融。模拟 HTTP 与真实模型分别存档，绝不启动求解。"""
import argparse
import asyncio
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import time

ROOT = Path(__file__).resolve().parents[1]


async def main(args):
    import httpx
    from app.agent.runtime import AgentRuntime
    from app.config import settings_store
    from app.orchestration.research import ResearchDiscussion
    from app.orchestration.research_contracts import ResearchLimits
    from app.providers.manager import ProviderManager, ProviderStatus
    from app.session.store import SessionStore
    root = args.output.resolve()
    root.mkdir(parents=True, exist_ok=False)
    dataset_path = ROOT/'backend/tests/fixtures/research_comparison.json'
    dataset = json.loads(dataset_path.read_text(encoding='utf-8'))
    original = settings_store.load()
    provider_name = args.provider or original.get('active_provider')
    if args.mode == 'real_model' and (not provider_name or provider_name not in original['providers']):
        raise ValueError('Select an existing configured provider explicitly')
    class Configuration:
        def load(self):
            config = deepcopy(original)
            selected = provider_name if args.mode == 'real_model' else 'deepseek'
            config['active_provider'] = selected
            for key, value in config['providers'].items(): value['enabled'] = key == selected
            if args.mode == 'synthetic': config['providers']['deepseek'].update(base_url='https://fixture.invalid', model='synthetic-fixture')
            return config
        def secret(self, name): return settings_store.secret(name) if args.mode == 'real_model' else ''
    async def synthetic(request):
        opinion = {'summary': 'Deterministic transport fixture; no real model inference.', 'claims': [
            {'claim_id': 'bounded-premise', 'statement': 'No scientific calculation has been authorized.',
             'evidence_refs': ['research-goal'], 'conditions': ['benchmark'], 'uncertainty': 'source premises only',
             'counterexamples': [], 'questions': []}], 'requests': []}
        return httpx.Response(200, json={'choices': [{'finish_reason': 'stop', 'message': {'content': json.dumps(opinion)}}],
            'usage': {'prompt_tokens': 0, 'completion_tokens': 0}})
    class SyntheticProvider(ProviderManager):
        async def preferred(self):
            return ProviderStatus('deepseek', 'Synthetic HTTP fixture', 'synthetic', True, 'Ready', '',
                                  'https://fixture.invalid', 'synthetic-fixture')
    provider = ProviderManager(Configuration()) if args.mode == 'real_model' else SyntheticProvider(Configuration(), httpx.MockTransport(synthetic))
    sessions = SessionStore(root/'sessions.db'); sessions.initialize()
    runtime = AgentRuntime(sessions=sessions, providers=provider)
    service = ResearchDiscussion(runtime.controller, root, provider)
    limits = ResearchLimits(model_calls=16, token_limit=500000, output_tokens=1800, concurrency=3)
    results = []
    for case in dataset['cases']:
        for arm in ('single_agent', 'parallel_no_collaboration', 'full_rework', 'sky'):
            sid = sessions.create('workbench', case['case_id'] + ': ' + arm)['id']
            started = time.perf_counter()
            result = await service.run(sid, case['goal'], case['evidence'], limits=limits, strategy=arm)
            elapsed = time.perf_counter() - started
            (root/(case['case_id']+'-'+arm+'.json')).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
            receipts = [call['receipt'] for call in result['calls']]
            known = all(row.get('usage_known') for row in receipts)
            row = {'case': case['case_id'], 'arm': arm, 'status': result['status'], 'mode': args.mode,
                'correct_safe_disposition': result['status'] == case['expected_readiness'],
                'logical_model_requests': sum(item.get('requests') or 0 for item in receipts),
                'actual_remote_requests': sum(item.get('requests') or 0 for item in receipts) if args.mode == 'real_model' else 0,
                'input_tokens': sum(item.get('input_tokens') or 0 for item in receipts) if known and args.mode == 'real_model' else None,
                'output_tokens': sum(item.get('output_tokens') or 0 for item in receipts) if known and args.mode == 'real_model' else None,
                'elapsed_seconds': round(elapsed, 3), 'solver_submissions': 0, 'duplicate_run_count': 0,
                'scientific_completion_rate': None, 'physical_pass_rate': None,
                'disagreement_resolved': False if case['case_id'] == 'boundary_conflict' else None,
                'accepted_opinions': len(result['outcome']['accepted_opinions']),
                'full_rework_tasks': result['outcome'].get('full_rework_tasks', 0),
                'monetary_cost': None, 'cost_note': 'Token usage only; no price or solver data is invented'}
            results.append(row)
            report = {'dataset_sha256': hashlib.sha256(dataset_path.read_bytes()).hexdigest(), 'limits': limits.model_dump(),
                'mode': args.mode, 'data_origin': dataset['data_origin'], 'results': results,
                'limitations': 'Evidence is intentionally incomplete. All arms share fail-closed gates. This is not a comparison of actual scientific solver accuracy; no arm is presumed best.'}
            (root/'metrics.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
            print(json.dumps(row, ensure_ascii=False), flush=True)


if __name__=='__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--mode', choices=['synthetic', 'real_model'], required=True)
    parser.add_argument('--provider')
    asyncio.run(main(parser.parse_args()))
