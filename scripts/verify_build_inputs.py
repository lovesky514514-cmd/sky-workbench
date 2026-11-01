"""Check the locked environment and bounded public build inputs."""

import hashlib
import importlib.metadata as metadata
import json
from pathlib import Path
import re
import sys
import ssl


ROOT = Path(__file__).resolve().parents[1]


def verify():
    lock=ROOT/'packaging/requirements-win-py312.lock'
    expected={}
    for line in lock.read_text(encoding='utf-8').splitlines():
        match=re.match(r'([A-Za-z0-9_.-]+)==([^ ]+) --hash=sha256:([a-f0-9]{64})$',line)
        if match:expected[match[1].lower().replace('_','-')]=match[2]
    failures=[]
    if sys.version_info[:2]!=(3,12) or sys.maxsize<=2**32:failures.append('Python 3.12 x64 is required')
    installed={d.metadata['Name'].lower().replace('_','-'):d.version for d in metadata.distributions()}
    for name,version in expected.items():
        if installed.get(name)!=version:failures.append('Locked distribution mismatch: '+name)
    extras=set(installed)-set(expected)-{'pip'}
    if extras:failures.append('Unexpected build distributions: '+','.join(sorted(extras)))
    native=json.loads((ROOT/'packaging/native-inputs.json').read_text(encoding='utf-8'))
    for name,digest in native['files'].items():
        path=ROOT/'packaging/native'/name
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest()!=digest:
            failures.append('Pinned native input missing or changed: '+name)
    companion=native['ssl_companion']
    ssl_source=Path(ssl.__file__).read_bytes()
    if companion['filename'].encode() in ssl_source:
        path=ROOT/'packaging/tls'/companion['filename']
        if (not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest()!=companion['sha256']
                or hashlib.sha256(ssl_source).hexdigest()!=companion['ssl_source_sha256']):
            failures.append('Pinned SSL implementation or companion data mismatch')
    folder=ROOT/'knowledge/public'
    manifest=json.loads((folder/'manifest.json').read_text(encoding='utf-8'))
    if manifest.get('vendor_manuals_included') is not False:failures.append('Public knowledge policy is not explicit')
    for name,digest in manifest['files'].items():
        if name!='units.json' or hashlib.sha256((folder/name).read_bytes()).hexdigest()!=digest:
            failures.append('Public knowledge hash or filename mismatch')
    license_manifest=json.loads((ROOT/'frontend/src-tauri/resources/licenses/LICENSE_MANIFEST.json').read_text(encoding='utf-8'))
    if license_manifest.get('build_lock_sha256')!=hashlib.sha256(lock.read_bytes()).hexdigest():
        failures.append('License collection is not bound to this dependency lock')
    if license_manifest.get('unknown_components'):failures.append('Unresolved notice collection')
    notices={row['name'].lower().replace('_','-'):row['version'] for row in license_manifest.get('python',[])}
    if notices!=expected or any(not row.get('files') for row in license_manifest.get('python',[])):
        failures.append('Notice set differs from the locked Python dependency closure')
    if license_manifest.get('rust_resolution',{}).get('cargo_lock_sha256')!=hashlib.sha256((ROOT/'frontend/src-tauri/Cargo.lock').read_bytes()).hexdigest():
        failures.append('Rust notices are not bound to this Cargo lock')
    if license_manifest.get('frontend_lock_sha256')!=hashlib.sha256((ROOT/'frontend/package-lock.json').read_bytes()).hexdigest():
        failures.append('Frontend notices are not bound to this npm lock')
    return {'passed':not failures,'locked_distributions':len(expected),'native_inputs':len(native['files']),
            'public_knowledge_units':manifest['units'],'failures':failures}


if __name__=='__main__':
    report=verify();print(json.dumps(report,indent=2));raise SystemExit(0 if report['passed'] else 1)
