import { useEffect, useState } from 'react'
import { api } from '../api'
import type { AtomPoint, EngineRun, EngineTaskPayload, TaskEngine, TaskInputs } from '../engineTasks'
import type { Locale } from '../i18n'
import { AtomStructureView, EngineResultView } from './EngineResultView'

const initialLJ = { kind: 'lammps_lj' as const, cells: 3, density: 0.8442, temperature: 1, timestep: 0.005, steps: 1000, seed: 87287, equilibration_steps: 1000, thermo_interval: 10, dump_interval: 100 }

export function EngineTaskWorkspace({ engine, projectId, locale }: { engine: TaskEngine; projectId: string; locale: Locale }) {
  const zh = locale === 'zh-CN'
  const [lj, setLJ] = useState(initialLJ)
  const [files, setFiles] = useState<Record<string, string>>({})
  const [atoms, setAtoms] = useState('O 0 0 0\nH 0.9572 0 0\nH -0.239 0.927 0')
  const [forcefield, setForcefield] = useState('Universal')
  const [executable, setExecutable] = useState('')
  const [runs, setRuns] = useState<EngineRun[]>([])
  const [run, setRun] = useState<EngineRun | null>(null)
  const [preview, setPreview] = useState<Record<string, string> | null>(null)
  const [inputStructure, setInputStructure] = useState<{ atoms: AtomPoint[]; units: string } | null>(null)
  const [confirmed, setConfirmed] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const active = Boolean(run && ['PENDING', 'RUNNING', 'CANCEL_REQUESTED'].includes(run.state))

  useEffect(() => {
    let current = true
    setConfirmed(false); setPreview(null); setInputStructure(null); setRun(null); setRuns([])
    void Promise.all([api.engineConfiguration(), api.engineRuns(projectId)]).then(([config, history]) => {
      if (!current) return
      setExecutable(config.executables[engine] ?? '')
      const matching = history.runs.filter((item) => item.engine === engine)
      setRuns(matching); setRun(matching[0] ?? null)
    }).catch(() => { if (current) setError(zh ? '无法读取任务记录。' : 'Could not load task records.') })
    return () => { current = false }
  }, [engine, projectId, zh])

  useEffect(() => {
    if (!run) return
    let current = true
    const refresh = () => {
      void api.engineRun(run.run_id).then((next) => {
        if (!current) return
        setRun(next); setRuns((previous) => [next, ...previous.filter((item) => item.run_id !== next.run_id)])
      }).catch(() => { if (current) setError(zh ? '状态读取失败，请稍后重试。' : 'Could not read run status. Retry shortly.') })
    }
    refresh()
    const timer = active ? window.setInterval(refresh, 800) : undefined
    return () => { current = false; window.clearInterval(timer) }
  }, [active, run?.run_id, zh, engine, projectId])

  const payload = (): EngineTaskPayload => {
    let inputs: TaskInputs = lj
    if (engine === 'VASP') inputs = { kind: 'vasp_files', files }
    if (engine === 'Materials Studio') inputs = { kind: 'ms_forcite_energy', forcefield,
      atoms: atoms.split(/\r?\n/).map((line) => line.trim()).filter(Boolean).map((line) => {
        const [element, x, y, z, extra] = line.trim().split(/\s+/)
        if (!element || extra || [x, y, z].some((value) => value == null || !Number.isFinite(Number(value)))) throw new Error('Invalid atom coordinate')
        return { element, x: Number(x), y: Number(y), z: Number(z) }
      }) }
    return { title: engine === 'LAMMPS' ? 'LJ fluid · NVT' : engine === 'VASP' ? 'VASP local task' : 'Forcite single-point energy',
      project_id: projectId, inputs, confirmed, timeout_seconds: engine === 'VASP' ? 3600 : 600 }
  }
  const perform = async (action: () => Promise<void>) => {
    setBusy(true); setError('')
    try { await action() }
    catch (cause) { setError(cause instanceof Error ? cause.message : 'Task unavailable') }
    finally { setBusy(false) }
  }
  const changed = () => { setConfirmed(false); setPreview(null); setInputStructure(null) }
  const importFile = async (name: string, file?: File) => {
    if (!file) return
    await perform(async () => {
      if (file.size > 8_000_000) throw new Error('Maximum file size: 8 MB')
      const text = await file.text()
      setFiles((current) => ({ ...current, [name]: text })); changed()
    })
  }

  return <section className="warm-page engine-task-page">
    <div className="warm-page-heading"><div><h1>{engine}</h1><p>{zh ? '配置参数 → 检查输入 → 运行 → 查看结构与曲线' : 'Configure → review inputs → run → inspect structure and curves'}</p></div>
      <span className="warm-status-pill">{run?.state ?? (zh ? '待配置任务' : 'Task draft')}</span></div>
    {run && <p role="status">{zh ? '验证状态' : 'Verification'}: {run.verification_status ?? 'UNVERIFIED'} · {run.scientific_validation}
      {zh ? '（任务结束、输出可读与科学验证分别记录）' : ' (process, readback and scientific validation are separate)'}</p>}
    <div className="engine-task-layout"><aside className="engine-task-form">
      <h2>{zh ? '任务参数' : 'Task parameters'}</h2>
      {engine === 'LAMMPS' && <><p>{zh ? '单原子 LJ 流体探索案例；约化单位，不对应已验证的实际材料。' : 'Exploratory monoatomic LJ fluid in reduced units.'}</p>
        {Object.entries(lj).filter(([key]) => key !== 'kind').map(([key, value]) => <label key={key}>{key}
          <input type="number" step="any" value={value} onChange={(event) => { setLJ((current) => ({ ...current, [key]: Number(event.target.value) })); changed() }} /></label>)}
      </>}
      {engine === 'VASP' && <><p>{zh ? '导入完整输入；POTCAR 由你本地的有效授权提供。当前适配器执行本地单任务。' : 'Import complete inputs and a locally licensed POTCAR. Local single-task execution.'}</p>
        {['INCAR', 'POSCAR', 'KPOINTS', 'POTCAR'].map((name) => <label key={name}>{name} {files[name] ? '✓' : ''}
          <input type="file" onChange={(event) => void importFile(name, event.target.files?.[0])} /></label>)}
      </>}
      {engine === 'Materials Studio' && <><p>{zh ? 'Forcite 单点能；输入原子坐标（Å），每行：元素 x y z。' : 'Forcite single-point energy. Atom coordinates (Å): element x y z per line.'}</p>
        <textarea aria-label="Atom coordinates" rows={7} value={atoms} onChange={(event) => { setAtoms(event.target.value); changed() }} />
        <label>{zh ? '力场' : 'Force field'}<select value={forcefield} onChange={(event) => { setForcefield(event.target.value); changed() }}>
          {['Universal', 'COMPASS', 'COMPASSII'].map((name) => <option key={name}>{name}</option>)}</select></label>
        <p>{zh ? 'CASTEP / DMol³ 尚未实现实机验收。此入口不代表所有模块可用。' : 'CASTEP / DMol³ are not validated here. This connector does not certify all modules.'}</p>
      </>}
      <details><summary>{zh ? '本机执行程序' : 'Local executable'}</summary>
        <label>{engine === 'Materials Studio' ? 'RunMatScript' : engine}<input value={executable} onChange={(event) => { setExecutable(event.target.value); changed() }} placeholder={zh ? '绝对路径；留空使用环境变量或 PATH' : 'Absolute path; leave empty to use environment or PATH'} /></label>
        <button disabled={busy || active} onClick={() => void perform(async () => { await api.saveEngineConfiguration(engine, executable) })}>{zh ? '保存路径' : 'Save path'}</button>
        <button disabled={busy || active} onClick={() => void perform(async () => {
          await api.saveEngineConfiguration(engine, executable)
          const connection = await api.connectEngine(engine)
          if (connection.status !== 'HANDSHAKE_PASSED') throw new Error(connection.detail ?? (zh ? '连接尚未验证。' : 'Connector is not verified.'))
        })}>{zh ? '测试连接（不执行模型）' : 'Test connector (no model calculation)'}</button>
        <p>{zh ? '检测或配置路径不代表许可证、版本和科学结果已经验证。' : 'A configured path does not verify licenses, versions or scientific results.'}</p>
      </details>
      <button disabled={busy} onClick={() => void perform(async () => { const value = await api.previewEngineTask(payload()); setPreview(value.files); setInputStructure(value.structure) })}>{zh ? '预览结构与检查输入' : 'Preview structure and inputs'}</button>
      <label className="warm-script-consent"><input type="checkbox" checked={confirmed} onChange={(event) => setConfirmed(event.target.checked)} />
        {zh ? '接受本次参数与假设，授权本机运行' : 'Accept parameters and assumptions; authorize local execution'}</label>
      {active ? <button disabled={busy} onClick={() => void perform(async () => { if (run) setRun(await api.cancelEngineTask(run.run_id)) })}>{zh ? '停止本次任务' : 'Stop this task'}</button>
        : <button disabled={busy || !confirmed} onClick={() => void perform(async () => {
          const next = await api.startEngineTask(payload()); setRun(next); setRuns((current) => [next, ...current])
        })}>{zh ? '开始计算' : 'Start calculation'}</button>}
      {error && <p role="alert">{error}</p>}
    </aside><main className="engine-task-output">
      {runs.length > 0 && <label>{zh ? '运行记录（每次参数独立保存）' : 'Run history (parameters saved per run)'}
        <select value={run?.run_id ?? ''} disabled={active} onChange={(event) => setRun(runs.find((item) => item.run_id === event.target.value) ?? null)}>
          {runs.map((item) => <option key={item.run_id} value={item.run_id}>{new Date(item.created_at * 1000).toLocaleString()} · {item.state} · {item.run_id.slice(0, 8)}</option>)}
        </select></label>}
      {run?.result ? <EngineResultView key={run.run_id} result={run.result} zh={zh} />
        : <div className="engine-empty"><h2>{zh ? '模型与计算结果' : 'Model and results'}</h2><p>{active ? (zh ? '引擎运行中，完成后读取真实产物。' : 'Engine running; actual artifacts will be collected.') : (zh ? '配置任务并运行后，在此查看真实结构、轨迹和曲线。' : 'Run a configured task to inspect actual structures, trajectories and curves.')}</p></div>}
      {run && <details><summary>{zh ? '本次参数、假设与运行日志' : 'Run parameters, assumptions and log'}</summary>
        <p>{run.scientific_validation} · {run.elapsed_ms} ms</p>{run.detail && <p>{run.detail}</p>}
        {run.assumptions.map((item) => <p key={item}>{item}</p>)}<pre>{JSON.stringify(run.request.inputs, null, 2)}</pre><pre>{run.output}</pre>
        <ul>{run.artifacts.map((item) => <li key={item.name}><button disabled={busy} onClick={() => void perform(() => api.downloadEngineArtifact(run.run_id, item.name))}>{item.name}</button> · {item.size_bytes} bytes · SHA-256 {item.sha256}</li>)}</ul>
        {!!run.omitted_artifacts?.length && <ul>{run.omitted_artifacts.map((item) => <li key={item.name}>{item.name}: {item.reason} ({item.size_bytes} bytes)</li>)}</ul>}
      </details>}
      {inputStructure && <AtomStructureView atoms={inputStructure.atoms} caption={`${zh ? '输入结构 · 尚未求解' : 'Input structure · not solved'} · ${inputStructure.units}`} />}
      {preview && <details><summary>{zh ? '高级：输入文件预览' : 'Advanced: input file preview'}</summary>
        {Object.entries(preview).map(([name, text]) => <details key={name}><summary>{name}</summary><pre>{text}</pre></details>)}
      </details>}
    </main></div>
  </section>
}
