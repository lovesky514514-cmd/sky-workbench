"""Bind a Windows wheel environment to official PyPI digests."""

import argparse
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]


def verify(item):
    name, version = item['metadata']['name'], item['metadata']['version']
    digest = item['download_info']['archive_info']['hashes']['sha256']
    url = f'https://pypi.org/pypi/{name}/{version}/json'
    with urlopen(Request(url, headers={'User-Agent': 'SKY-Workbench-3.1-build-audit'}), timeout=30) as response:
        official = json.load(response)
    match = next((entry for entry in official['urls'] if entry['digests']['sha256'] == digest), None)
    if match is None:
        raise ValueError(f'{name} {version}: installed wheel hash is absent from official PyPI metadata')
    return {'name': name, 'version': version, 'sha256': digest, 'official_url': match['url'],
            'downloaded_from': item['download_info']['url'], 'filename': match['filename'],
            'license_expression': official['info'].get('license_expression'),
            'declared_license': official['info'].get('license'), 'verified': True}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--install-report', type=Path, required=True)
    parser.add_argument('--evidence', type=Path, required=True)
    args = parser.parse_args()
    report = json.loads(args.install_report.read_text(encoding='utf-8'))
    with ThreadPoolExecutor(max_workers=6) as pool:
        rows = sorted(pool.map(verify, report['install']), key=lambda row: row['name'].lower())
    lock = ['# Windows x64 / Python 3.12; official wheel hashes verified for SKY 3.1.0.',
            '--only-binary=:all:', '--require-hashes']
    lock += [f"{row['name']}=={row['version']} --hash=sha256:{row['sha256']}" for row in rows]
    (ROOT/'packaging'/'requirements-win-py312.lock').write_text('\n'.join(lock)+'\n',encoding='utf-8')
    args.evidence.write_text(json.dumps({'verified_wheels':len(rows),'wheels':rows},ensure_ascii=False,indent=2),encoding='utf-8')
    print(f'Verified {len(rows)} wheel hashes against official PyPI metadata')


if __name__ == '__main__':
    main()
