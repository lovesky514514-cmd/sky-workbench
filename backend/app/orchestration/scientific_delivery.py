"""从冻结账本导出科研成果 / Export scientific results from the frozen ledger."""
import hashlib
import json
from pathlib import Path
import re
import zipfile

from app.execution.files import bounded_read, reject_reparse
from app.redaction import redact_object, redact_text


def digest(data):
    return hashlib.sha256(data).hexdigest()


class ScientificDelivery:
    def __init__(self, sessions, workspace):
        self.sessions = sessions
        self.workspace = Path(workspace)

    def snapshot(self, run_id, project_id):
        if not re.fullmatch(r'[A-Za-z0-9-]{1,80}',run_id):
            raise KeyError(run_id)
        run=self.sessions.get_run(run_id)
        session=self.sessions.get(run['session_id'])
        if session['project_id']!=project_id:
            raise KeyError(run_id)
        if run['outcome']!='Completed' or run['cancel_requested'] or not run['attempt_id']:
            raise ValueError('scientific_delivery_requires_completed_attempt')
        contract=self.sessions.completed_evidence(run_id)
        if not contract or contract.get('attempt_id')!=run['attempt_id'] or contract.get('run_id')!=run_id:
            raise ValueError('completed_evidence_identity_missing')
        with self.sessions._connect() as db:
            events=[json.loads(row['payload_json']) for row in db.execute(
                "SELECT payload_json FROM events WHERE session_id=? AND event_type='run.completed' ORDER BY sequence DESC",(run['session_id'],))]
            plans=[json.loads(row['payload_json']) for row in db.execute(
                "SELECT payload_json FROM events WHERE session_id=? AND event_type='plan.updated'",(run['session_id'],))]
        completed=next((v for v in events if v.get('run_id')==run_id and v.get('attempt_id')==run['attempt_id']),None)
        if not completed or completed.get('execution_verdict',{}).get('completed') is not True:
            raise ValueError('scientific_verdict_not_complete')
        # 按摘要找原计划 / Find the original plan by its recorded digest.
        plan=next((v for v in reversed(plans) if digest(json.dumps(v,sort_keys=True,ensure_ascii=False).encode())==run['plan_hash']),None)
        return run,contract,completed,plan

    def export(self, run_id, project_id):
        run, contract, completed, plan = self.snapshot(run_id, project_id)
        payloads = {}
        originals = {}
        total = 0
        for key,entry in sorted(contract['artifacts'].items()):
            if not re.fullmatch(r'[A-Za-z0-9_-]{1,80}',key):
                raise ValueError('invalid_artifact_key')
            path = Path(entry['path'])
            reject_reparse(path)
            path.resolve().relative_to(self.workspace.resolve())
            if str(path) not in run['artifacts'].values():
                raise ValueError('artifact_not_in_final_journal')
            total+=entry['bytes'] if 'bytes' in entry else entry.get('size_bytes',path.stat().st_size)
            if total>512_000_000:
                raise ValueError('delivery_size_limit')
            data=bounded_read(path.parent,path.name,256_000_000)
            if digest(data)!=entry['sha256']:
                raise ValueError('artifact_integrity_lost')
            name='artifacts/'+key+path.suffix.lower()
            originals[name]=entry['sha256']
            suffix = path.suffix.lower()
            if suffix in {'.log', '.md', '.txt', '.json'}:
                # 仅日志副本容许解码替换 / Only log copies allow replacement decoding.
                encoding_errors = 'replace' if suffix == '.log' else 'strict'
                data = redact_text(data.decode('utf-8', errors=encoding_errors)).encode('utf-8')
            payloads[name]=data
        record={'kind':'scientific_run','data_origin':'journal_completed_solver_artifacts',
            'run_id':run_id,'attempt_id':run['attempt_id'],'session_id':run['session_id'],'project_id':project_id,
            'plan_sha256':run['plan_hash'],'plan_snapshot_available':plan is not None,
            'plan':plan,'execution':completed,'attempt_count':len(run['attempts'])}
        payloads['run.json']=json.dumps(redact_object(record),ensure_ascii=False,indent=2,allow_nan=False).encode()
        lines=['# SKY 科研成果报告','','数据来源：工作台已完成的求解器运行与冻结产物；未重新求解。',
            '',f'RUN：{run_id}',f'Attempt：{run["attempt_id"]}',f'计划摘要：{run["plan_hash"]}',
            '','## 验证结果','',json.dumps(completed['execution_verdict'],ensure_ascii=False),
            '','数值、单位和原始检查见 run.json 与 artifacts；参数属于该次计划的适用范围，不代表材料实验标定。',
            '若缺少原始场数据，本包不会用解析值补造场或图片。历史运行缺失的计划或独立审核记录明确保留为不可用。',
            '','## 产物与来源','']
        lines.extend('- '+name+' · 原始 SHA-256 '+value for name,value in originals.items())
        lines.extend(['','日志、文字报告及 JSON 已脱敏；manifest 记录导出字节摘要，original_sha256 记录清洗前摘要。',
            '原生日志中的非 UTF-8 字节在导出副本中替换为 Unicode 替换字符。',
            '本地模型和数值文件可能含项目资料；包未上传或加入源码仓库。',''])
        payloads['report.md']=redact_text('\n'.join(lines)).encode()
        manifest={'schema_version':1,'kind':'scientific_run','scientific_status':'COMPLETED_AT_RECORDED_SCOPE',
            'run_id':run_id,'attempt_id':run['attempt_id'],'plan_sha256':run['plan_hash'],
            'files':{name:{'sha256':digest(data),'bytes':len(data),
                **({'original_sha256':originals[name]} if name in originals else {})} for name,data in payloads.items()}}
        raw=json.dumps(manifest,ensure_ascii=False,sort_keys=True,indent=2).encode()
        sha=digest(raw)
        folder=self.workspace/'scientific_reports'/run_id/sha
        reject_reparse(folder)
        archive=folder/'scientific-result.zip'
        if archive.exists():
            self.verify(archive,sha)
        else:
            folder.mkdir(parents=True,exist_ok=True)
            with zipfile.ZipFile(archive,'x',compression=zipfile.ZIP_DEFLATED) as out:
                for name,data in {**payloads,'manifest.json':raw,'manifest.sha256':sha.encode()}.items():
                    info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
                    info.compress_type = zipfile.ZIP_DEFLATED
                    out.writestr(info,data)
            self.verify(archive,sha)
        # 发布前再次核对尝试 / Recheck the attempt before returning the archive.
        current=self.sessions.get_run(run_id)
        if current['attempt_id']!=run['attempt_id'] or current['outcome']!='Completed' or current['cancel_requested']:
            raise ValueError('delivery_state_changed')
        return archive,sha

    @staticmethod
    def verify(archive,expected_sha):
        reject_reparse(Path(archive))
        with zipfile.ZipFile(archive) as data:
            raw=data.read('manifest.json')
            if digest(raw)!=expected_sha or data.read('manifest.sha256').decode()!=expected_sha:
                raise ValueError('delivery_manifest_changed')
            manifest=json.loads(raw)
            if set(data.namelist())!=set(manifest['files'])|{'manifest.json','manifest.sha256'} or len(data.namelist())!=len(set(data.namelist())):
                raise ValueError('delivery_archive_members_changed')
            for name,entry in manifest['files'].items():
                body=data.read(name)
                if len(body)!=entry['bytes'] or digest(body)!=entry['sha256']:
                    raise ValueError('delivery_bytes_changed')
        return manifest
