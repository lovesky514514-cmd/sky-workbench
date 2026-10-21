"""Manual real-COMSOL smoke probe for one generated MPH; never runs in public-safe tests."""

import asyncio
import json
import sys
from pathlib import Path

from app.app_paths import app_paths
from app.comsol.bridge_process import java_bridge_process_manager
from app.comsol.java_bridge import java_bridge_client
from app.services.imported_mph import ImportedMphStore
from app.session.store import session_store


async def main(source: Path) -> int:
    # The probe copies a generated test model into the same isolated import
    # layout used by the UI. ModelUtil receives only its working.mph path.
    async def chunks():
        with source.open('rb') as stream:
            while block := stream.read(1024 * 1024):
                yield block
                await asyncio.sleep(0)

    store = ImportedMphStore(app_paths.models_dir)
    item = await store.save(source.name, chunks())
    session_store.initialize()
    result = {'import_id': item['id'], 'source_sha256': item['sha256'], 'preview_verified': False}
    try:
        await java_bridge_process_manager.start(str(store.working_path(item['id'])))
        for _ in range(120):
            status = java_bridge_process_manager.status()
            if status.get('state') in {'ready', 'failed', 'crashed'}:
                break
            await asyncio.sleep(1)
        result['bridge_state'] = status.get('state')
        result['bridge_error'] = str(status.get('error', ''))[:500]
        if status.get('state') == 'ready':
            health = await java_bridge_client.health()
            tree = await java_bridge_client.model_tree()
            result['comsol_version'] = health.get('comsol_version')
            result['model_tree'] = tree
            result['preview_verified'] = bool(health.get('real_comsol') and health.get('connected') and tree.get('model_state_verified'))
        return 0 if result['preview_verified'] else 1
    finally:
        if java_bridge_process_manager.status().get('running') or java_bridge_process_manager.status().get('state') == 'starting':
            await java_bridge_process_manager.stop()
        result['post_stop_state'] = java_bridge_process_manager.status().get('state')
        output = app_paths.workspace_dir / 'import_preview_probe.json'
        output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps(result, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    raise SystemExit(asyncio.run(main(Path(sys.argv[1]))))
