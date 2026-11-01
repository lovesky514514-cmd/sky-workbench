import { useState } from 'react'
import type { AtomPoint, EngineResult } from '../engineTasks'

export function AtomStructureView({ atoms, caption }: { atoms: AtomPoint[]; caption: string }) {
  const [angle, setAngle] = useState(25)
  if (!atoms.length) return null
  const radians = angle * Math.PI / 180
  const points = atoms.map((atom) => ({ ...atom,
    x: atom.position[0] * Math.cos(radians) - atom.position[2] * Math.sin(radians),
    y: atom.position[1] - 0.3 * (atom.position[0] * Math.sin(radians) + atom.position[2] * Math.cos(radians)),
  }))
  const xs = points.map((atom) => atom.x), ys = points.map((atom) => atom.y)
  const xmin = Math.min(...xs), ymin = Math.min(...ys)
  const span = Math.max(Math.max(...xs) - xmin, Math.max(...ys) - ymin, 1)
  return <figure className="engine-structure">
    <svg viewBox="0 0 600 360" role="img" aria-label={caption}>
      {points.map((atom) => <circle key={atom.id} cx={80 + (atom.x - xmin) / span * 420} cy={300 - (atom.y - ymin) / span * 250}
        r={atoms.length > 500 ? 2.5 : 6} fill={atom.element === 'H' ? '#96a5a1' : atom.element === 'O' ? '#b66b52' : '#52847b'}>
        <title>{atom.element} #{atom.id}: {atom.position.map((v) => v.toPrecision(5)).join(', ')}</title>
      </circle>)}
    </svg>
    <label>↻ <input aria-label="Rotate structure" type="range" min="0" max="360" value={angle} onChange={(event) => setAngle(Number(event.target.value))} /></label>
    <figcaption>{caption} · {atoms.length} atoms</figcaption>
  </figure>
}

export function EngineResultView({ result, zh }: { result: EngineResult; zh: boolean }) {
  const [frameIndex, setFrameIndex] = useState(0)
  const [column, setColumn] = useState(1)
  const frames = result.frames ?? []
  const frame = frames[Math.min(frameIndex, frames.length - 1)]
  const series = result.series ?? []
  const columns = result.columns ?? []
  const yColumn = Math.min(column, Math.max(1, columns.length - 1))
  const finite = series.filter((row) => Number.isFinite(row[0]) && Number.isFinite(row[yColumn]))
  const xMin = Math.min(...finite.map((row) => row[0])), xMax = Math.max(...finite.map((row) => row[0]))
  const yMin = Math.min(...finite.map((row) => row[yColumn])), yMax = Math.max(...finite.map((row) => row[yColumn]))
  const path = finite.map((row, index) => `${index ? 'L' : 'M'}${60 + (row[0] - xMin) / (xMax - xMin || 1) * 500},${290 - (row[yColumn] - yMin) / (yMax - yMin || 1) * 250}`).join(' ')
  return <section className="engine-results">
    <h2>{zh ? '模型与真实计算结果' : 'Model and collected results'}</h2>
    <p>{result.units}</p>
    {frame && <><AtomStructureView atoms={frame.atoms} caption={`Trajectory · step ${frame.step}`} />
      <label>{zh ? '轨迹帧' : 'Trajectory frame'} <input type="range" min="0" max={frames.length - 1} value={Math.min(frameIndex, frames.length - 1)} onChange={(event) => setFrameIndex(Number(event.target.value))} /></label></>}
    {result.structure && <AtomStructureView atoms={result.structure.atoms} caption={zh ? '引擎输出结构 · Å' : 'Engine output structure · Å'} />}
    {result.energy != null && <p className="engine-energy">{result.energy.toPrecision(8)} {result.units}</p>}
    {finite.length > 0 && <figure className="engine-chart">
      <select aria-label={zh ? '选择曲线' : 'Choose curve'} value={yColumn} onChange={(event) => setColumn(Number(event.target.value))}>
        {columns.slice(1).map((label, i) => <option key={label} value={i + 1}>{label}</option>)}
      </select>
      <svg viewBox="0 0 600 340" role="img" aria-label={columns[yColumn]}>
        <path d="M60 40V290H560" fill="none" stroke="#9aa9a4" />
        <path d={path} fill="none" stroke="#52847b" strokeWidth="2" />
        <text x="4" y="44">{yMax.toPrecision(4)}</text><text x="4" y="289">{yMin.toPrecision(4)}</text>
        <text x="60" y="315">{xMin}</text><text x="500" y="315">{xMax}</text>
      </svg><figcaption>{columns[0]} → {columns[yColumn]} · {result.units}</figcaption>
    </figure>}
    <ul>{Object.entries(result.checks).map(([name, passed]) => <li key={name}>{passed ? '✓' : '△'} {name}</li>)}</ul>
    {result.preview && <p role="status">{zh ? '轨迹预览' : 'Trajectory preview'}: {result.preview.shown_frames}/{result.preview.total_frames}
      {zh ? ` 帧；每帧最多显示 ${result.preview.max_atoms_per_frame} 个原子。完整数据保存在 ` : ` frames; up to ${result.preview.max_atoms_per_frame} atoms per frame. Full data: `}{result.preview.full_data}</p>}
    {result.validation_records && <div><h3>{zh ? '采样与物理诊断' : 'Sampling and physical diagnostics'}</h3>
      {Object.entries(result.validation_records).map(([name, record]) => <details key={name}><summary>{name}: {String(record.status)}</summary><pre>{JSON.stringify(record, null, 2)}</pre></details>)}
    </div>}
    <p>{result.limitations}</p>
  </section>
}
