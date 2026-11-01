"""Run a named check with an exact command, source digest and isolated workspace."""

import argparse
from datetime import UTC, datetime
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time


ROOT = Path(__file__).resolve().parents[1]
TARGETED_TESTS = (
    'tests/test_review_boundaries_v31.py',
    'tests/test_engine_contracts_v31.py',
    'tests/test_engine_api_v31.py',
    'tests/test_knowledge_contracts_v31.py',
)


def snapshot():
    paths = subprocess.check_output(['git','-C',str(ROOT),'ls-files','--cached','--others','--exclude-standard'],
                                    text=True,encoding='utf-8').splitlines()
    records = {}
    for name in sorted(set(paths)):
        path = ROOT/name
        if path.is_file() and path.suffix in {'.py','.ts','.tsx','.mjs','.cjs','.java','.css','.html','.json','.toml','.rs','.ps1','.lock','.spec','.ini','.nsh','.pem'}:
            records[name] = hashlib.sha256(path.read_bytes()).hexdigest()
    digest = hashlib.sha256(json.dumps(records,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    return digest,records


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--suite', choices=['backend','backend-targeted','frontend','frontend-build'], required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    output = args.out.resolve()
    if output.exists() or not output.is_relative_to(ROOT.parent):
        raise SystemExit('Use a new verification output directory inside the workspace')
    output.mkdir(parents=True)
    temporary = output/'temp'; temporary.mkdir()
    environment = {key:value for key,value in os.environ.items()
                   if not any(word in key.upper() for word in ['TOKEN','SECRET','API_KEY','PASSWORD'])}
    environment.update(TEMP=str(temporary),TMP=str(temporary),PYTHONIOENCODING='utf-8',
                       PYTHONPATH=str(ROOT/'backend'),
                       COMSOL_WORKBENCH_WORKSPACE_DIR=str(output/'workspace'),
                       COMSOL_WORKBENCH_APP_DATA_DIR=str(output/'appdata'))
    if args.suite in {'backend', 'backend-targeted'}:
        selection = ['tests'] if args.suite == 'backend' else list(TARGETED_TESTS)
        command=[str(ROOT/'.venv/Scripts/python.exe'),'-X','utf8','-m','pytest',*selection,'-q',
                 '--basetemp',str(output/'pytest'),'-ra']
        cwd=ROOT/'backend'
    elif args.suite=='frontend':
        command=['node','--test',*[str(path) for path in sorted((ROOT/'frontend/tests').glob('*.test.mjs'))]]
        cwd=ROOT/'frontend'
    else:
        command=['npm.cmd','run','build'];cwd=ROOT/'frontend'
    before,records=snapshot()
    revision=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip()
    started=time.monotonic()
    header={'suite':args.suite,'command':command,'cwd':str(cwd),'git_commit':revision,
            'source_sha256_before':before,'started_at':datetime.now(UTC).isoformat()}
    with (output/'output.log').open('w',encoding='utf-8') as log:
        log.write(json.dumps(header,ensure_ascii=False)+'\n');log.flush()
        process=subprocess.run(command,cwd=cwd,env=environment,stdout=log,stderr=subprocess.STDOUT,check=False)
    after,_=snapshot()
    result={**header,'source_sha256_after':after,'source_unchanged':before==after,
            'exit_code':process.returncode,'elapsed_seconds':round(time.monotonic()-started,3),
            'log_sha256':hashlib.sha256((output/'output.log').read_bytes()).hexdigest()}
    (output/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    (output/'source_files.json').write_text(json.dumps(records,indent=2),encoding='utf-8')
    print(json.dumps(result,ensure_ascii=False))
    raise SystemExit(0 if process.returncode==0 and before==after else 1)


if __name__=='__main__':
    main()
