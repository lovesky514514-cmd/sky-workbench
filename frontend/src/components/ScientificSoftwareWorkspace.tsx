import { lazy, Suspense, useEffect, useState } from 'react'
import { api, type LocalScriptRun } from '../api'
import type { Locale } from '../i18n'
import { supportedFigureBytes } from '../localFigure'
import type { ScriptLanguage } from './CodeWorkbenchEditor'
import { MathicsFormula } from './MathicsFormula'

const CodeWorkbenchEditor = lazy(() => import('./CodeWorkbenchEditor')
  .then((module) => ({ default: module.CodeWorkbenchEditor })))

export type SoftwareEngine = 'Python' | 'MATLAB' | 'Wolfram' | 'Mathics3' | 'Materials Studio'

type Adapter = { engine: string; installed: boolean; probe: string; readback: string; execute: string }
type Preview = { name: string; url: string } | null
type Props = { engine: SoftwareEngine; sessionId: string | null; locale: Locale; onOpenSettings: () => void }

const languages: Record<SoftwareEngine, ScriptLanguage> = {
  Python: 'python', MATLAB: 'matlab', Wolfram: 'wolfram', Mathics3: 'wolfram', 'Materials Studio': 'materialsscript',
}
const extensions: Record<SoftwareEngine, string> = {
  Python: 'py', MATLAB: 'm', Wolfram: 'wl', Mathics3: 'wl', 'Materials Studio': 'pl',
}

function draftKey(sessionId: string | null, engine: SoftwareEngine) {
  return `sky-script-draft:v1:${sessionId ?? 'scratch'}:${engine}`
}

function initialDraft(key: string) {
  try { return localStorage.getItem(key) ?? '' } catch { return '' }
}

export function ScientificSoftwareWorkspace({ engine, sessionId, locale, onOpenSettings }: Props) {
  const copy = (cn: string, en: string) => locale === 'zh-CN' ? cn : en
  const key = draftKey(sessionId, engine)
  const runKey = `${key}:last-run`
  const [draft, setDraft] = useState(() => initialDraft(key))
  const [saved, setSaved] = useState(true)
  const [adapters, setAdapters] = useState<Adapter[] | null>(null)
  const [probe, setProbe] = useState<string | null>(null)
  const [probing, setProbing] = useState(false)
  const [preview, setPreview] = useState<Preview>(null)
  const [figureError, setFigureError] = useState(false)
  const [runnerAvailable, setRunnerAvailable] = useState(false)
  const [run, setRun] = useState<LocalScriptRun | null>(null)
  const [runError, setRunError] = useState(false)
  const [confirmed, setConfirmed] = useState(false)
  const [figureIndex, setFigureIndex] = useState(0)
  const [runFigure, setRunFigure] = useState<string | null>(null)
  const [draftFile, setDraftFile] = useState<string | null>(null)
  const [draftError, setDraftError] = useState(false)
  const adapter = adapters?.find((item) => item.engine === engine)

  // Discovery is read-only. A detected executable or handshake is not a
  // connected project, an exported figure, or scientific validation.
  useEffect(() => {
    let active = true
    void api.simulationAdapters().then((data) => { if (active) setAdapters(data.adapters) })
      .catch(() => { if (active) setAdapters([]) })
    return () => { active = false }
  }, [])

  useEffect(() => {
    let active = true
    void api.localScriptAvailability().then(({ engines }) => { if (active) setRunnerAvailable(engines[engine] === true) })
      .catch(() => { if (active) setRunnerAvailable(false) })
    return () => { active = false }
  }, [engine])

  useEffect(() => {
    let active = true
    let previous = ''
    try { previous = sessionStorage.getItem(runKey) ?? '' } catch { /* Storage may be disabled. */ }
    if (previous) void api.localScriptStatus(previous).then((value) => {
      if (active) setRun((current) => current ?? value)
    })
      .catch(() => { if (active) { setRunError(true); try { sessionStorage.removeItem(runKey) } catch { /* Best effort. */ } } })
    return () => { active = false }
  }, [runKey])

  useEffect(() => {
    if (!run || !['PENDING', 'RUNNING'].includes(run.state)) return
    let active = true
    const timer = window.setInterval(() => {
      void api.localScriptStatus(run.run_id).then((next) => {
        if (active) setRun((current) => current?.run_id === next.run_id ? next : current)
      })
        .catch(() => { if (active) setRunError(true) })
    }, 700)
    return () => { active = false; window.clearInterval(timer) }
  }, [run?.run_id, run?.state])

  useEffect(() => {
    const figure = run?.state === 'COMPLETED' ? run.figures[figureIndex] : null
    if (!figure || !run) { setRunFigure(null); return }
    setRunFigure(null)
    let active = true
    let url = ''
    void api.localScriptFigureUrl(run.run_id, figure.name).then((value) => {
      if (!active) { URL.revokeObjectURL(value); return }
      url = value; setRunFigure(value)
    }).catch(() => { if (active) setRunError(true) })
    return () => { active = false; if (url) URL.revokeObjectURL(url) }
  }, [run?.run_id, run?.state, run?.figures, figureIndex])

  useEffect(() => () => { if (preview) URL.revokeObjectURL(preview.url) }, [preview])

  const edit = (value: string) => {
    if (value.length > 200_000) return
    setDraft(value)
    setConfirmed(false)
    try { localStorage.setItem(key, value); setSaved(true) }
    catch { setSaved(false) }
  }

  const exportScript = () => {
    const url = URL.createObjectURL(new Blob([draft], { type: 'text/plain;charset=utf-8' }))
    const link = document.createElement('a')
    link.href = url
    link.download = `workbench-draft.${extensions[engine]}`
    link.click()
    window.setTimeout(() => URL.revokeObjectURL(url), 60_000)
  }

  const importScript = async (file?: File) => {
    if (!file) return
    if (file.size > 200_000 || !/\.(wl|wls)$/i.test(file.name)) { setDraftError(true); return }
    try { edit(await file.text()); setDraftFile(file.name); setDraftError(false) }
    catch { setDraftError(true) }
  }

  const inspectImage = async (file?: File) => {
    if (!file) return
    // A local figure is display-only. It is never sent to Runtime or counted
    // as an execution artifact because its run identity is unknown.
    try {
      const bytes = new Uint8Array(await file.slice(0, 12).arrayBuffer())
      if (!supportedFigureBytes(file.type, file.size, bytes)) { setFigureError(true); return }
      setFigureError(false)
      setPreview({ name: file.name, url: URL.createObjectURL(file) })
    } catch { setFigureError(true) }
  }

  const startScript = async () => {
    if (!confirmed || !runnerAvailable || !draft.trim() || run?.state === 'RUNNING') return
    setRunError(false); setFigureIndex(0); setRunFigure(null)
    try {
      const started = await api.startLocalScript(engine, draft)
      setRun(started)
      try { sessionStorage.setItem(runKey, started.run_id) } catch { /* The current view still tracks the run. */ }
    }
    catch { setRunError(true) }
  }

  const cancelScript = async () => {
    if (!run) return
    try { setRun(await api.cancelLocalScript(run.run_id)) }
    catch { setRunError(true) }
  }

  const handshake = async () => {
    if (!adapter || adapter.probe !== 'AVAILABLE_ON_REQUEST' || probing) return
    setProbing(true)
    try { setProbe((await api.probeSimulationAdapter(engine)).status) }
    catch { setProbe('HANDSHAKE_FAILED') }
    finally { setProbing(false) }
  }

  const installed = runnerAvailable || adapter?.installed === true
  const status = adapters === null ? copy('检测中', 'Detecting')
    : engine === 'Mathics3' ? (runnerAvailable
      ? copy('独立 Mathics3 内核可用 · 结果未认证', 'External Mathics3 kernel available · results unverified')
      : copy('独立 Mathics3 内核未就绪', 'External Mathics3 kernel unavailable'))
    : engine === 'Python' && !runnerAvailable ? copy('编辑器可用 · 本机 Python 未检测到', 'Editor ready · local Python not detected')
      : !installed ? copy('未检测到本机软件', 'Software not detected')
        : probe === 'HANDSHAKE_PASSED' ? copy('本机握手通过 · 模型未同步', 'Local handshake passed · model not synchronized')
          : copy('已检测 · 模型未同步', 'Detected · model not synchronized')

  return <section className="warm-page warm-science-page warm-script-page">
    <div className="warm-page-heading">
      <div><h1>{engine === 'Mathics3' ? 'Math Workspace · Mathics3' : engine}</h1><p>{copy('代码编辑、主动运行与本地结果预览；脚本输出不构成科学认证。',
        'Edit, explicitly run and preview local outputs; script output is not scientific certification.')}</p></div>
      <span className="warm-status-pill">{status}</span>
    </div>
    <div className="warm-script-layout">
      <div className="warm-script-workarea">
        <header><strong>{copy('代码工作区', 'Code workspace')}</strong><span>{`${draftFile ?? `draft.${extensions[engine]}`} · ${saved ? copy('已保存到本机', 'Saved locally') : copy('本机保存失败', 'Local save failed')}`}</span></header>
        <Suspense fallback={<p className="warm-script-loading">{copy('正在加载编辑器…', 'Loading editor…')}</p>}>
          <CodeWorkbenchEditor language={languages[engine]} value={draft} onChange={edit}
            ariaLabel={`${engine} ${copy('代码编辑器', 'code editor')}`} />
        </Suspense>
        <footer>
          <span>{copy('草稿保存在本机。请勿在脚本中填写密钥。', 'Draft saved locally. Do not put secrets in scripts.')}</span>
          {engine === 'Mathics3' && <label className="warm-script-image-pick">{copy('打开 .wl/.wls', 'Open .wl/.wls')}
            <input type="file" accept=".wl,.wls" onChange={(event) => void importScript(event.target.files?.[0])} /></label>}
          {draftError && <span role="alert">{copy('只能打开不超过 200 KB 的 .wl/.wls 草稿。', 'Open a .wl/.wls draft of at most 200 KB.')}</span>}
          <button type="button" onClick={exportScript} disabled={!draft}>{copy('导出脚本', 'Export script')}</button>
        </footer>
      </div>
      <aside className="warm-script-side">
        <section><h2>{copy('本机连接', 'Local connection')}</h2>
          <p>{status}</p>
          {engine === 'Materials Studio' && <p>{copy('需核实已授权的 MaterialsScript 版本；当前没有安全的模型读回接口。',
            'A licensed MaterialsScript version must be verified; no safe model readback is connected yet.')}</p>}
          {engine === 'Python' && !runnerAvailable && <p>{copy('未发现独立 Python 解释器；内置科学 Runtime 不用于执行任意草稿。',
            'No separate Python interpreter found. The scientific Runtime does not execute arbitrary drafts.')}</p>}
          {engine === 'Mathics3' && <p>{copy('Mathics3 是可选的独立本地内核；需另行安装。它不是 Wolfram Engine，也不会回退到 Wolfram。',
            'Mathics3 is an optional, separately installed local kernel. It is not Wolfram Engine and never falls back to it.')}</p>}
          {adapter?.probe === 'AVAILABLE_ON_REQUEST' &&
            <button type="button" onClick={() => void handshake()} disabled={probing}>
              {probing ? copy('正在测试…', 'Testing…') : copy('测试本机引擎', 'Test local engine')}</button>}
          {probe && <p role="status">{probe === 'HANDSHAKE_PASSED'
            ? copy('握手通过；尚未读取模型或运行脚本。', 'Handshake passed; no model readback or script execution.') :
            `${copy('连接结果', 'Connection result')}: ${probe}`}</p>}
          <button type="button" onClick={onOpenSettings}>{copy('打开连接设置', 'Open connection settings')}</button>
        </section>
        <section><h2>{copy('运行草稿', 'Run draft')}</h2>
          <p>{copy('只运行你审查过的脚本。脚本以当前 Windows 用户权限运行，并非安全沙箱；不修改 COMSOL 科学认证。',
            'Run only code you reviewed. It runs with your Windows account, not in a security sandbox; COMSOL certification is unaffected.')}</p>
          <label className="warm-script-consent"><input type="checkbox" checked={confirmed} onChange={(event) => setConfirmed(event.target.checked)} />
            {copy('我已检查此脚本并同意本地执行', 'I reviewed this script and approve local execution')}</label>
          {run?.state === 'PENDING' || run?.state === 'RUNNING'
            ? <button type="button" onClick={() => void cancelScript()}>{copy('停止脚本', 'Stop script')}</button>
            : <button type="button" onClick={() => void startScript()} disabled={!confirmed || !runnerAvailable || !draft.trim() || engine === 'Materials Studio'}>
                {copy('运行脚本', 'Run script')}</button>}
          {!runnerAvailable && <p>{copy('没有可用的本机脚本运行器。编辑和导出仍可用。',
            'No local script runner is available. Editing and export remain available.')}</p>}
          {run && <div className="warm-script-run-result"><strong>{run.state} · {run.elapsed_ms} ms</strong>
            {run.mathics_result && <><pre>{run.mathics_result.text || run.mathics_result.messages.join('\n')}</pre>
              <MathicsFormula mathml={run.mathics_result.mathml} latex={run.mathics_result.latex} />
              {run.mathics_result.latex && <details><summary>LaTeX</summary><code>{run.mathics_result.latex}</code></details>}</>}
            {run.output && <pre>{run.output}</pre>}
            <small>{copy('脚本输出未验证；不是模型状态或科学结论。',
              'Script output is unverified; it is not model state or a scientific conclusion.')}</small></div>}
          {runError && <p role="alert">{copy('运行状态或输出无法读取。请检查本地 Runtime。',
            'Run status or output unavailable. Check the local Runtime.')}</p>}
        </section>
        <section><h2>{copy('导出结果预览', 'Exported figure preview')}</h2>
          <p>{copy('可查看本机 PNG、JPG 或 WebP；来源与科学结论仍未验证。',
            'View a local PNG, JPG or WebP; provenance and scientific claims remain unverified.')}</p>
          {run?.state === 'COMPLETED' && run.figures.length > 0 && <figure>
            {runFigure ? <img src={runFigure} alt={run.figures[figureIndex].name} /> : <p>{copy('正在读取图像…', 'Loading figure…')}</p>}
            <figcaption>{run.figures[figureIndex].name} · {figureIndex + 1}/{run.figures.length} · {copy('未验证', 'Unverified')}</figcaption>
            {runFigure && <a className="warm-script-save-figure" href={runFigure} download={run.figures[figureIndex].name}>
              {copy('保存这张图片', 'Save this figure')}</a>}
            {run.figures.length > 1 && <nav className="warm-script-figure-nav">
              <button type="button" onClick={() => setFigureIndex((figureIndex + run.figures.length - 1) % run.figures.length)}>{copy('上一张', 'Previous')}</button>
              <button type="button" onClick={() => setFigureIndex((figureIndex + 1) % run.figures.length)}>{copy('下一张', 'Next')}</button>
            </nav>}
          </figure>}
          <label className="warm-script-image-pick">{copy('选择本地图像', 'Choose a local image')}
            <input type="file" accept="image/png,image/jpeg,image/webp" onChange={(event) => void inspectImage(event.target.files?.[0])} />
          </label>
          {figureError && <p role="alert">{copy('只接受不超过 20 MB 的有效 PNG、JPG 或 WebP。',
            'Only valid PNG, JPG or WebP files up to 20 MB can be previewed.')}</p>}
          {preview ? <figure><img src={preview.url} alt={preview.name} /><figcaption>{preview.name} · {copy('未验证', 'Unverified')}</figcaption></figure>
            : <p className="warm-script-empty">{copy('暂无可预览结果', 'No figure selected')}</p>}
        </section>
      </aside>
    </div>
  </section>
}
