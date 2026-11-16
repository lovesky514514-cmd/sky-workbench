"""Communication Expert 与 Agent Security Expert 的固定 WASM 调用。"""
import json
import os
from pathlib import Path
import shutil
import subprocess

from .contracts import BoundaryError, Document, digest
from .storage import no_links


ASSETS = Path(__file__).resolve().parent/'sandbox'
FLAGS = ('prompt_injection','malicious_tool','memory_poisoning','active_content_or_route',
         'sensitive_marker','cross_agent_propagation')


class Lanes:
    def __init__(self, root):
        self.root = root

    def _execute(self, lane, raw, *, mode=None):
        if not 0 < len(raw) <= 65_536:
            raise BoundaryError('isolated_worker_input_limit')
        node = shutil.which('node')
        if node is None:
            raise BoundaryError('isolated_worker_host_unavailable')
        cwd = self.root/lane
        no_links(cwd)
        if not cwd.is_dir():
            raise BoundaryError('isolated_worker_directory_missing')
        manifest = json.loads((ASSETS/'manifest.json').read_bytes())
        module = ASSETS/(lane+'.wasm')
        if digest(module.read_bytes()) != manifest[lane]:
            raise BoundaryError('isolated_worker_integrity')
        env = {key:os.environ[key] for key in ('SystemRoot','WINDIR','SYSTEMROOT') if key in os.environ}
        try:
            result = subprocess.run([str(Path(node).resolve()), str(ASSETS/'runner.mjs'), mode or lane,
                str(module), manifest[lane]], cwd=cwd, env=env, input=raw, stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL, timeout=6,
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        except (OSError, subprocess.TimeoutExpired) as error:
            raise BoundaryError('isolated_worker_unavailable') from error
        if result.returncode or len(result.stdout) > len(raw):
            raise BoundaryError('isolated_worker_rejected_document')
        return result.stdout

    def normalize(self, document):
        body = self._execute('communication', document.body.encode('utf-8')).decode('utf-8')
        return Document.model_validate({**document.model_dump(), 'body':body})

    def task_transition(self, kind, current, opposite_direction, reply_matches):
        codes = {'offer':1, 'accept':2, 'reject':3}
        state = 0 if current is None else 1 if current == 'OFFERED' else 2
        value = self._execute('communication',bytes((codes[kind],state,opposite_direction,reply_matches)),
                              mode='communication-task')
        return {b'1':'OFFERED',b'2':'ACCEPTED',b'3':'REJECTED'}.get(value)

    def review(self, document):
        result = self._execute('security', (document.title+'\n'+document.body).encode('utf-8'))
        if not result.isdigit() or not 0 <= int(result) <= 63:
            raise BoundaryError('invalid_security_review')
        flags = [name for bit,name in enumerate(FLAGS) if int(result) & (1 << bit)]
        return {'engine':'wasm-static-v1','flags':flags, 'verdict':'quarantine' if flags else 'review_required'}
