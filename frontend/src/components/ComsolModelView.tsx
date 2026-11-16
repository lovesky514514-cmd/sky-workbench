import { lazy, Suspense, useEffect, useRef, useState, type ReactNode } from 'react'
import { api } from '../api'
import type { ModelSource } from '../comsolTopology'
import { safeUiError } from '../safeUiError'
import './comsol-model-view.css'

const Viewport = lazy(() => import('./ComsolViewport'))

export function ComsolModelView({ source, zh, fallback }: { source: ModelSource; zh: boolean; fallback?: ReactNode }) {
  const [snapshot, setSnapshot] = useState<Awaited<ReturnType<typeof api.modelVisualization>> | null>(null)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [fresh, setFresh] = useState(true)
  const active = useRef<AbortController | null>(null)
  const current = snapshot?.manifest.project_id === source.projectId && snapshot.manifest.model_identity === source.id &&
    snapshot.manifest.source_kind === source.kind &&
    (!('runId' in source) || (snapshot.manifest.run_id === source.runId && snapshot.manifest.attempt_id === source.attemptId)) &&
    (source.kind !== 'checkpoint' || snapshot.manifest.checkpoint_index === source.index) ? snapshot : null
  const { kind, id, projectId } = source
  const runKey = 'runId' in source ? `${source.runId}:${source.attemptId}` : ''
  const index = source.kind === 'checkpoint' ? source.index : null

  async function refresh(model: ModelSource) {
    active.current?.abort()
    const controller = new AbortController()
    active.current = controller
    setBusy(true); setError('')
    try {
      const data = await api.modelVisualization(model, controller.signal)
      if (!controller.signal.aborted) { setFresh(true); setSnapshot(data) }
    } catch (cause) {
      if (!controller.signal.aborted) { setFresh(false); setError(safeUiError(cause)) }
    } finally {
      if (!controller.signal.aborted) setBusy(false)
    }
  }

  useEffect(() => {
    if (kind === 'imported') void refresh(source)
    if (kind === 'checkpoint' || kind === 'heat-result') void refresh(source)
    return () => active.current?.abort()
  }, [kind, id, projectId, runKey, index])

  useEffect(() => {
    if (!current || kind === 'checkpoint' || busy) return
    const control = new AbortController()
    const check = () => {
      void api.checkModelVisualization(source, current.manifest.source_model_revision, control.signal)
        .then(ok => { if (!control.signal.aborted) setFresh(ok) })
        .catch(() => { if (!control.signal.aborted) setFresh(false) })
    }
    window.addEventListener('focus', check)
    const timer = window.setInterval(() => { if (!document.hidden) check() }, 30_000)
    return () => { control.abort(); window.removeEventListener('focus', check); window.clearInterval(timer) }
  }, [current, busy, kind, id, projectId, runKey])

  return <section className="comsol-model-view">
    <header><strong>{zh ? 'COMSOL 模型读回' : 'COMSOL model readback'}</strong>
      <button disabled={busy} onClick={() => void refresh(source)}>{zh ? '刷新模型' : 'Refresh model'}</button></header>
    <p>{kind === 'checkpoint' ? (zh ? `当前 RUN 网格检查点 #${source.index} · 求解前只读快照` :
      `Active RUN mesh checkpoint #${source.index} · pre-solve preview`) :
      kind === 'heat-result' ? (zh ? '从当前通过科学审核的 MPH 读取最终时刻网格顶点温度。相邻顶点之间使用线性着色。' :
        'Final-time nodal temperature from the verified saved MPH. Colors are interpolated between vertices.') :
      (zh ? '当前来源为工作台持有的已保存 MPH。外部 COMSOL 修改请另存后重新导入。' :
        'Source: saved Workbench MPH. Save edits in external COMSOL and reimport the saved file.')}</p>
    {busy && <p role="status">{zh ? '正在读取真实模型与已有网格…' : 'Reading COMSOL geometry and existing mesh…'}</p>}
    {(error || current && !fresh) && <p role="alert">
      {current && !fresh ? (zh ? '历史快照 · 当前来源无法核对，请刷新。' : 'Historical snapshot · refresh to verify the current source.') : ''} {error}</p>}
    {current && (kind !== 'heat-result' || fresh) ? <>
      <Suspense fallback={<p>{zh ? '正在加载三维画布…' : 'Loading viewport…'}</p>}>
        <Viewport manifest={current.manifest} surfaces={current.surfaces} tetrahedra={current.tetrahedra}
          temperature={current.temperature} zh={zh} />
      </Suspense>
      <p className="comsol-readback-id">
        {current.manifest.representation_type} · COMSOL {current.manifest.comsol_version} · {current.manifest.component_tag}/{current.manifest.geometry_tag}
        <br />{zh ? '修订' : 'Revision'} <code>{current.manifest.source_model_revision.slice(0, 16)}</code> · {new Date(current.manifest.created_at).toLocaleString()}
        <br />SHA-256 <code>{current.manifest.artifact.sha256}</code>
      </p>
    </> : !busy && (fallback ?? <p>{zh ? '尚无已核验的几何或网格。' : 'No verified geometry or mesh is available.'}</p>)}
  </section>
}
