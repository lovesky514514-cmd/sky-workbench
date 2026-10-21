"""RC4.2 visible installer bootstrap for a verified external Lite payload.

The executable and payload directory must travel together. This source is not
evidence that a new Setup.exe has been built or clean-installed.
"""
from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import traceback
import uuid


PRODUCT = 'COMSOL Scientific Workbench RC4.2'
VERSION = '1.1.0-rc4.2'
MANIFEST = 'RC42_PAYLOAD_MANIFEST.json'


def _log(message: str, log_path: Path) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open('a', encoding='utf-8') as stream:
        stream.write(message + '\n')


def _notice(title: str, message: str, *, headless: bool, error: bool = False,
            question: bool = False) -> bool:
    if headless:
        print(f'{title}: {message}', file=sys.stderr if error else sys.stdout)
        return True
    flags = 0x10 if error else 0x24 if question else 0x40
    return ctypes.windll.user32.MessageBoxW(None, message, title, flags) == 6 if question else bool(
        ctypes.windll.user32.MessageBoxW(None, message, title, flags))


def _digest(path: Path) -> str:
    sha = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            sha.update(chunk)
    return sha.hexdigest()


def _verified_payload(source: Path) -> dict:
    manifest = source / MANIFEST
    data = json.loads(manifest.read_text(encoding='utf-8'))
    if data.get('product') != PRODUCT or data.get('version') != VERSION:
        raise ValueError('payload identity mismatch')
    if data.get('model_weights_included') is not False or data.get('proprietary_comsol_content_included') is not False:
        raise ValueError('payload content policy mismatch')
    entries = data.get('entries')
    if not isinstance(entries, list) or not entries:
        raise ValueError('payload manifest is empty')
    root = source.resolve()
    for record in entries:
        relative = Path(str(record['path']))
        target = (root / relative).resolve()
        if root not in target.parents or not target.is_file():
            raise ValueError('payload entry is missing or outside source')
        if target.stat().st_size != record['bytes'] or _digest(target) != record['sha256']:
            raise ValueError('payload integrity mismatch')
    return data


def _safe_target(path: Path) -> Path:
    path = path.resolve()
    if path == Path(path.anchor) or path == Path.home().resolve() or len(path.parts) < 4:
        raise ValueError('unsafe install target')
    return path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=PRODUCT)
    parser.add_argument('--target', type=Path)
    parser.add_argument('--payload', type=Path, help='Verified payload directory (diagnostic use)')
    parser.add_argument('--log', type=Path)
    parser.add_argument('--uninstall', action='store_true')
    parser.add_argument('--headless', action='store_true')
    parser.add_argument('--no-launch', action='store_true')
    args = parser.parse_args(argv)
    base = Path(os.environ.get('LOCALAPPDATA') or Path.home() / 'AppData' / 'Local')
    log_path = args.log or base / 'COMSOL Scientific Workbench' / 'rc42-setup.log'
    target = _safe_target(args.target or base / 'Programs' / PRODUCT)
    executable = Path(sys.executable if getattr(sys, 'frozen', False) else __file__).resolve()
    source = (args.payload or executable.parent / 'installer_payload' / 'COMSOL Scientific Workbench').resolve()
    try:
        _log(f'RC4.2 setup operation={"uninstall" if args.uninstall else "install"} target={target}', log_path)
        if args.uninstall:
            if not target.is_dir() or not (target / MANIFEST).is_file():
                raise FileNotFoundError('installed RC4.2 payload not found')
            _verified_payload(target)
            if not _notice(PRODUCT, 'Uninstall this verified Workbench installation?',
                           headless=args.headless, question=True):
                return 1
            shutil.rmtree(target)
            _log('uninstall complete', log_path)
            _notice(PRODUCT, 'Uninstall complete.', headless=args.headless)
            return 0
        _verified_payload(source)
        if target.exists():
            raise FileExistsError('install target already exists; existing data was not changed')
        if not _notice(PRODUCT, 'Install COMSOL Scientific Workbench RC4.2?',
                       headless=args.headless, question=True):
            return 1
        target.parent.mkdir(parents=True, exist_ok=True)
        staging = target.with_name(target.name + '.installing-' + uuid.uuid4().hex[:8])
        try:
            shutil.copytree(source, staging)
            _verified_payload(staging)
            staging.rename(target)
        except BaseException:
            if staging.is_dir():
                shutil.rmtree(staging)
            raise
        _log('install complete', log_path)
        _notice(PRODUCT, 'Installation complete. The Workbench is ready to start.', headless=args.headless)
        if not args.no_launch:
            subprocess.Popen([str(target / 'START_RC42.exe')], cwd=target)
        return 0
    except Exception as exc:
        diagnostic_id = uuid.uuid4().hex[:12]
        try:
            _log(f'FAILED diagnostic_id={diagnostic_id}\n{traceback.format_exc()}', log_path)
        except OSError:
            # Even an unwritable log location must not turn into silent exit.
            print(f'RC4.2 Setup failure; diagnostic ID: {diagnostic_id}', file=sys.stderr)
        _notice(PRODUCT + ' — FAILED',
                f'Installation did not complete. Existing files were not replaced. Diagnostic ID: {diagnostic_id}.',
                headless=args.headless, error=True)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
