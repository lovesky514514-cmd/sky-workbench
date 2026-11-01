"""Stage an offline native input bundle only after checking every pinned hash."""

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import zipfile


ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--libffi', type=Path, required=True)
    parser.add_argument('--openssl-dir', type=Path, required=True)
    parser.add_argument('--archive', type=Path, required=True)
    args = parser.parse_args()
    manifest = ROOT/'packaging'/'native-inputs.json'
    expected = json.loads(manifest.read_text(encoding='utf-8'))['files']
    inputs = {'libffi-8.dll':args.libffi, **{name:args.openssl_dir/name for name in expected if name!='libffi-8.dll'}}
    for name, path in inputs.items():
        if hashlib.sha256(path.read_bytes()).hexdigest()!=expected[name]:
            raise ValueError(f'Pinned native input changed: {name}')
    target = ROOT/'packaging'/'native'
    target.mkdir(exist_ok=True)
    for name, path in inputs.items():
        shutil.copyfile(path,target/name)
    args.archive.parent.mkdir(parents=True,exist_ok=True)
    with zipfile.ZipFile(args.archive,'w',compression=zipfile.ZIP_DEFLATED) as archive:
        archive.write(manifest,'native-inputs.json')
        for name in expected:
            archive.write(target/name,'native/'+name)
    evidence = {'status':'VERIFIED_RECOVERED_BASELINE_INPUTS','archive_sha256':hashlib.sha256(args.archive.read_bytes()).hexdigest(),
                'files':{name:{'sha256':expected[name],'recovered_from':str(path)} for name,path in inputs.items()}}
    args.archive.with_suffix('.json').write_text(json.dumps(evidence,indent=2),encoding='utf-8')
    print('Verified and staged all three native inputs')


if __name__=='__main__':
    main()
