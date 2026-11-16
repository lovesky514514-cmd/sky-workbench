"""用 clang/wasm-ld 构建两个无 WASI 依赖的模块。"""
import hashlib
import json
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parent


def main():
    manifest = {}
    for lane in ('communication', 'security'):
        out = ROOT / f'{lane}.wasm'
        negotiation = ['-Wl,--export=negotiate'] if lane == 'communication' else []
        subprocess.run(['clang', '--target=wasm32', '-Oz', '-fno-builtin', '-nostdlib',
            '-Wl,--no-entry,--export=buffer,--export=process,--export-memory,--initial-memory=131072,'
            '--max-memory=131072,--strip-all,-z,stack-size=16384', *negotiation, str(ROOT/f'{lane}.c'), '-o', str(out)], check=True)
        manifest[lane] = hashlib.sha256(out.read_bytes()).hexdigest()
    (ROOT/'manifest.json').write_text(json.dumps(manifest, sort_keys=True, indent=2)+'\n', encoding='ascii')


if __name__ == '__main__':
    main()
