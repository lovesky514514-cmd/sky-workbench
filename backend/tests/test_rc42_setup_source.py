"""Isolated source-level installer checks; not packaged Setup.exe acceptance."""
from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[2] / 'scripts' / 'rc42_setup.py'
SPEC = importlib.util.spec_from_file_location('rc42_setup', SCRIPT)
assert SPEC and SPEC.loader
setup = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(setup)


def payload(root: Path) -> Path:
    root.mkdir()
    files = {'Workbench.exe': b'controlled-workbench', 'START_RC42.exe': b'controlled-launcher'}
    entries = []
    for name, contents in files.items():
        path = root / name
        path.write_bytes(contents)
        entries.append({'path': name, 'bytes': len(contents),
                        'sha256': hashlib.sha256(contents).hexdigest()})
    (root / setup.MANIFEST).write_text(json.dumps({
        'product': setup.PRODUCT, 'version': setup.VERSION,
        'model_weights_included': False, 'proprietary_comsol_content_included': False,
        'entries': entries,
    }), encoding='utf-8')
    return root


def test_isolated_install_and_uninstall_with_log(tmp_path):
    source = payload(tmp_path / 'payload')
    target = tmp_path / 'isolated' / 'Programs' / 'Workbench RC42'
    log = tmp_path / 'setup.log'
    args = ['--headless', '--no-launch', '--payload', str(source),
            '--target', str(target), '--log', str(log)]
    assert setup.main(args) == 0
    assert (target / 'Workbench.exe').read_bytes() == b'controlled-workbench'
    assert setup.main(['--headless', '--uninstall', '--target', str(target), '--log', str(log)]) == 0
    assert not target.exists()
    assert 'install complete' in log.read_text(encoding='utf-8')
    assert 'uninstall complete' in log.read_text(encoding='utf-8')


def test_failure_is_logged_and_does_not_replace_existing_target(tmp_path):
    source = payload(tmp_path / 'payload')
    target = tmp_path / 'isolated' / 'Programs' / 'Workbench RC42'
    target.mkdir(parents=True)
    sentinel = target / 'user-data.txt'
    sentinel.write_text('keep', encoding='utf-8')
    log = tmp_path / 'setup.log'
    result = setup.main(['--headless', '--no-launch', '--payload', str(source),
                         '--target', str(target), '--log', str(log)])
    assert result == 2
    assert sentinel.read_text(encoding='utf-8') == 'keep'
    assert 'FAILED diagnostic_id=' in log.read_text(encoding='utf-8')
