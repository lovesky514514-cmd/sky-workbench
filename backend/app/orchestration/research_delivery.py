"""带摘要的可复现讨论交付物；不把研究意见转换为科学结果。"""
import hashlib
import json
from pathlib import Path
import re

from app.execution.files import reject_reparse
from app.orchestration.research_store import ResearchStore
from app.redaction import redact_object, redact_text


def sha(data):
    return hashlib.sha256(data).hexdigest()


class ResearchDelivery:
    def __init__(self, sessions, workspace):
        self.sessions, self.workspace = sessions, Path(workspace)

    def export(self, case_id):
        study = ResearchStore(self.sessions).read(case_id)
        if study['status'] == 'ACTIVE':
            raise ValueError('active_research_cannot_be_frozen_for_delivery')
        data = json.dumps(redact_object(study), ensure_ascii=False, sort_keys=True, indent=2).encode()
        identity = sha(data)
        if not re.fullmatch(r'[a-f0-9]{32}', case_id):
            raise ValueError('invalid_research_delivery_identity')
        folder = self.workspace / 'research_reports' / case_id / identity
        reject_reparse(folder)
        if (folder/'manifest.json').exists():
            return self.verify(folder)
        folder.mkdir(parents=True, exist_ok=False)
        calls = study['calls']
        known = all(item['receipt'].get('usage_known') is True for item in calls)
        total_requests = sum(item['receipt'].get('requests') or 0 for item in calls)
        def tokens(key):
            return str(sum(item['receipt'].get(key) or 0 for item in calls)) if known else 'UNKNOWN'
        lines = ['# SKY 科研讨论报告', '', '科学状态：NOT_RUN。此报告记录模型意见和证据，不证明实际求解成功。', '',
                 '研究目标：' + study['goal']['goal'], '', '讨论状态：' + study['status'],
                 f'实际模型请求：{total_requests}；输入 Token：{tokens("input_tokens")}；输出 Token：{tokens("output_tokens")}', '',
                 '## 专家与请求来源', '', '| 任务 | 模型 | 状态 |', '| --- | --- | --- |']
        for item in calls:
            lines.append(f'| {item["task_id"]} | {item["receipt"].get("model", "UNKNOWN")} | {item["status"]} |')
        lines.extend(['', '## 未决问题与最小返工', ''])
        for issue in (study.get('outcome') or {}).get('issues', []):
            lines.append('- ' + issue['kind'] + ': ' + issue['required'])
        for question in (study.get('outcome') or {}).get('open_questions', []):
            lines.append('- ' + question['question'])
        lines.extend(['', '所有科学主张及适用条件见 study.json 与冻结证据；模型调用输入、结构化契约和逐次用量包含在调用回执中。',
                      '没有原始数值场时不生成温度场或科研图片。真实结果仍须原 RUN 授权和独立科学审核。', ''])
        records = {}
        def write(name, content):
            path = folder/name
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open('xb') as file: file.write(content)
            records[name] = {'sha256': sha(content), 'bytes': len(content)}
        write('study.json', data)
        write('report.md', redact_text('\n'.join(lines)).encode())
        from app.execution.expert_contracts import ExpertScope
        scope = ExpertScope.model_validate(study['scope'])
        checkpoint = self.sessions.read_checkpoint(scope)
        vault_root = self.workspace / 'research_evidence' / scope.run_id / scope.attempt_id
        from app.execution.files import bounded_read
        evidence = []
        for ref in checkpoint.catalog():
            digest = ref.artifact.sha256 if ref.artifact else ref.locator
            if not re.fullmatch('[a-f0-9]{64}', digest):
                raise ValueError('unregistered_research_export_evidence')
            content = bounded_read(vault_root, digest + '.json', 2_000_000)
            if sha(content) != digest:
                raise ValueError('research_export_evidence_changed')
            name = 'evidence/' + digest + '.json'
            if name not in records: write(name, content)
            evidence.append({'evidence_id': ref.evidence_id, 'source_ref': ref.source_ref, 'file': name, 'sha256': digest})
        manifest = {'schema_version': 1, 'kind': 'research_discussion', 'scientific_status': 'NOT_RUN',
                    'scope': study['scope'], 'study_snapshot_sha256': identity, 'files': records, 'evidence': evidence}
        manifest_data = json.dumps(manifest, ensure_ascii=False, indent=2).encode()
        with (folder/'manifest.json').open('xb') as file: file.write(manifest_data)
        with (folder/'manifest.sha256').open('x', encoding='ascii') as file: file.write(sha(manifest_data))
        return self.verify(folder)

    def verify(self, folder):
        folder = Path(folder)
        reject_reparse(folder)
        folder.resolve().relative_to((self.workspace/'research_reports').resolve())
        data = (folder/'manifest.json').read_bytes()
        if sha(data) != (folder/'manifest.sha256').read_text(encoding='ascii'):
            raise ValueError('research_manifest_digest_mismatch')
        manifest = json.loads(data)
        for name, expected in manifest['files'].items():
            path = folder/name
            reject_reparse(path)
            path.resolve().relative_to(folder.resolve())
            content = path.read_bytes()
            if len(content) != expected['bytes'] or sha(content) != expected['sha256']:
                raise ValueError('research_delivery_file_changed')
        return {**manifest, 'manifest_sha256': sha(data), 'directory': str(folder)}
