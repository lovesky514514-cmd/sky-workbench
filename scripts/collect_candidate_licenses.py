"""Collect exact installed notices; engineering checks are not legal approval."""

import argparse
import hashlib
import importlib.metadata as metadata
import json
from pathlib import Path
import re
import sys


ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT/'frontend/src-tauri/resources/licenses'


def copy_notice(source: Path, relative: Path, origin: str) -> dict:
    target = TARGET/relative
    if not target.resolve().is_relative_to(TARGET.resolve()):
        raise ValueError('Unsafe notice destination')
    data = source.read_bytes()
    target.parent.mkdir(parents=True,exist_ok=True)
    target.write_bytes(data)
    return {'file':relative.as_posix(),'bytes':len(data),'sha256':hashlib.sha256(data).hexdigest().upper(),'source':origin}


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--wheel-verification',type=Path,required=True)
    parser.add_argument('--cargo-metadata',type=Path,required=True)
    args=parser.parse_args()
    wheel_report=json.loads(args.wheel_verification.read_text(encoding='utf-8'))
    cargo=json.loads(args.cargo_metadata.read_text(encoding='utf-8-sig'))
    manifest_path=TARGET/'LICENSE_MANIFEST.json'
    manifest=json.loads(manifest_path.read_text(encoding='utf-8'))
    baseline=TARGET/'BASELINE_3_0_MANIFEST.json'
    if not baseline.exists():baseline.write_bytes(manifest_path.read_bytes())
    rows=[];unknown=[]
    environment_root=Path(sys.prefix).resolve()
    for wheel in wheel_report['wheels']:
        distribution=metadata.distribution(wheel['name'])
        if distribution.version!=wheel['version'] or not wheel['verified']:
            raise ValueError('Installed dependency differs from the verified lock')
        files=[]
        for relative in distribution.files or []:
            name=Path(str(relative)).name.lower()
            if not any(token in name for token in ['license','licence','copying','notice']):continue
            source=Path(distribution.locate_file(relative)).resolve()
            if not source.is_relative_to(environment_root) or not source.is_file():continue
            safe_parts=[part for part in Path(str(relative)).parts if part not in {'.','..'}]
            target=Path('python-3.1')/f"{wheel['name']}-{wheel['version']}"/Path(*safe_parts)
            files.append(copy_notice(source,target,f"installed distribution RECORD: {wheel['name']} {wheel['version']}"))
        if not files:unknown.append(wheel['name'])
        rows.append({'ecosystem':'python','name':wheel['name'],'version':wheel['version'],
                     'declared_license':wheel['license_expression'] or wheel['declared_license'] or 'SEE_LICENSE_FILES',
                     'source_url':wheel['official_url'],'wheel_sha256':wheel['sha256'],'files':files,
                     'scope':'candidate build environment; frozen subset audited separately',
                     'status':'NOTICE_TEXT_COLLECTED' if files else 'NOTICE_MISSING'})
    nodes=[]
    lock=json.loads((ROOT/'frontend/package-lock.json').read_text(encoding='utf-8'))
    for path,entry in lock['packages'].items():
        if not path or entry.get('dev'):continue
        package_root=ROOT/'frontend'/path
        package=json.loads((package_root/'package.json').read_text(encoding='utf-8'))
        files=[]
        for candidate in package_root.iterdir():
            if candidate.is_file() and re.match(r'(?i)^(licen[sc]e|notice|copying)',candidate.name):
                relative=Path('node-3.1')/package['name'].replace('/','__')/package['version']/candidate.name
                files.append(copy_notice(candidate,relative,entry.get('resolved','package-lock.json')))
        if not files:unknown.append(package['name'])
        declared=package.get('license','SEE_LICENSE_FILES')
        nodes.append({'ecosystem':'node','name':package['name'],'version':package['version'],
                      'declared_license':declared,'source_url':entry.get('resolved'),
                      'integrity':entry.get('integrity'),'files':files,'status':'NOTICE_TEXT_COLLECTED' if files else 'NOTICE_MISSING'})
    graph={item['id']:item for item in cargo['resolve']['nodes']}
    packages={item['id']:item for item in cargo['packages']}
    visited=set();pending=[cargo['resolve']['root']]
    while pending:
        key=pending.pop()
        if key in visited:continue
        visited.add(key);pending.extend(item['pkg'] for item in graph[key]['deps'])
    old_rust={(item['name'],item['version']):item for item in manifest['rust']}
    rust=[]
    for key in sorted(visited):
        package=packages[key]
        if package['name']=='sky-workbench':continue
        pair=(package['name'],package['version'])
        if pair not in old_rust:raise ValueError('Collect exact crate notices before building: '+str(pair))
        rust.append(old_rust[pair])
    manifest.update(schema='sky-candidate-license-collection-v2',product_version='3.1.0',
                    status='NOTICE_COLLECTION_VERIFIED' if not unknown else 'NOTICE_COLLECTION_INCOMPLETE',
                    python=rows,node=nodes,rust=rust,unknown_components=unknown,
                    independent_legal_approval='NOT_PERFORMED',public_release_approved=False,
                    final_setup_payload_verified=False,
                    build_lock_sha256=hashlib.sha256((ROOT/'packaging/requirements-win-py312.lock').read_bytes()).hexdigest())
    manifest['rust_resolution']={'platform':'x86_64-pc-windows-msvc','target_packages':len(rust),
        'cargo_lock_sha256':hashlib.sha256((ROOT/'frontend/src-tauri/Cargo.lock').read_bytes()).hexdigest()}
    manifest['frontend_lock_sha256']=hashlib.sha256((ROOT/'frontend/package-lock.json').read_bytes()).hexdigest()
    manifest['first_party']=[copy_notice(ROOT/'LICENSE',Path('first-party')/'LICENSE','SKY Workbench owned source')]
    manifest_path.write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'python':len(rows),'node':len(nodes),'rust_target':len(rust),'missing_notices':unknown,
                      'status':manifest['status'],'public_release_approved':False}))
    raise SystemExit(1 if unknown else 0)


if __name__=='__main__':main()
