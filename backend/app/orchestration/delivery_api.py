"""本地完成产物下载；项目与字节校验先于文件响应。"""
import asyncio
from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from app.orchestration.scientific_delivery import ScientificDelivery


def delivery_router(sessions,workspace,active_project):
    router=APIRouter()
    @router.get('/api/scientific-runs/{run_id}/delivery')
    async def download(run_id:str):
        try:
            path,sha=await asyncio.to_thread(ScientificDelivery(sessions(),workspace).export,run_id,active_project())
        except KeyError as exc:
            raise HTTPException(404,'scientific_run_not_found') from exc
        except (ValueError,OSError) as exc:
            raise HTTPException(409,'scientific_delivery_not_ready_or_integrity_failed') from exc
        return FileResponse(path,media_type='application/zip',filename='scientific-result.zip',headers={'X-Manifest-SHA256':sha})
    return router
