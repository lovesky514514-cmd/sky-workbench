import { useEffect, useState } from 'react'
import type { Locale } from '../i18n'

type ScanStep = { id: string; state: string; current?: number }
type Props = { locale: Locale; scan: Record<string, unknown> }

// This component only renders Runtime's read-only discovery state. In
// particular, "detected" never means that a Bridge or scientific solve passed.
export function InstallationScanProgress({ locale, scan }: Props) {
  const zh = locale === 'zh-CN'
  const copy = (chinese: string, english: string) => zh ? chinese : english
  const steps = Array.isArray(scan.steps) ? scan.steps as ScanStep[] : []
  const [revealed, setRevealed] = useState(0)
  // A fast real scan may finish between 350 ms polls. Replay only its actual
  // completed statuses so users can follow each check; this is presentation,
  // not a fabricated extra scan or a scientific certification.
  useEffect(() => {
    if (scan.state !== 'completed') { setRevealed(0); return }
    if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
      setRevealed(steps.length)
      return
    }
    setRevealed(0)
    const timers = steps.map((_, index) => window.setTimeout(() => setRevealed(index + 1), 160 + index * 380))
    return () => timers.forEach(window.clearTimeout)
  }, [scan.state])
  const labels: Record<string, string> = {
    comsol: copy('检查 COMSOL 安装', 'Check COMSOL installation'),
    matlab: copy('查找 MATLAB', 'Find MATLAB'),
    wolfram: copy('查找 Wolfram / Mathematica', 'Find Wolfram / Mathematica'),
    materials_studio: copy('查找 Materials Studio', 'Find Materials Studio'),
    ollama_executable: copy('查找 Ollama 程序', 'Find Ollama application'),
    ollama_service: copy('连接 Ollama 本地服务', 'Connect to Ollama service'),
  }
  const stateLabel: Record<string, string> = {
    pending: copy('等待', 'Pending'),
    queued: copy('待展示', 'Awaiting display'),
    running: copy('检查中', 'Checking'),
    detected: copy('已检测到', 'Detected'),
    unavailable: copy('不可用', 'Unavailable'),
    failed: copy('检查失败', 'Check failed'),
  }
  const total = typeof scan.total === 'number' && scan.total > 0 ? scan.total : undefined
  const checked = typeof scan.checked === 'number' ? scan.checked : 0

  if (scan.state === 'idle') return null
  return <div className="warm-installation-scan" role="status" aria-live="polite">
    <div className="warm-installation-scan-heading"><strong>{copy('本机软件检查', 'Local software check')}</strong><span>{scan.state === 'running' ? copy('正在逐项检查', 'Checking each item') : scan.state === 'completed' && revealed < steps.length ? copy('逐项展示实际结果', 'Showing measured results') : scan.state === 'completed' && scan.partial_failure ? copy('部分检查失败', 'Some checks failed') : scan.state === 'completed' ? copy('检查完成', 'Check complete') : copy('检查未完成', 'Check incomplete')}</span></div>
    <ol className="warm-installation-scan-steps">
      {steps.map((step, index) => {
        const displayState = scan.state === 'completed' && index >= revealed ? 'queued' : step.state
        return <li key={`${step.id}-${displayState}`} className={`is-${displayState}`}>
        <span className="warm-installation-scan-indicator" aria-hidden="true" />
        <span className="warm-installation-scan-label">{labels[step.id] ?? step.id}</span>
        <strong>{stateLabel[displayState] ?? displayState}</strong>
        {step.id === 'comsol' && step.state === 'running' && <small>{total ? `${checked}/${total}` : copy('列举候选位置…', 'Listing candidates…')}</small>}
      </li>})}
    </ol>
    {scan.state === 'running' && steps.some((step) => step.id === 'comsol' && step.state === 'running') && <progress value={total ? checked : undefined} max={total} aria-label={copy('COMSOL 候选位置检查进度', 'COMSOL candidate check progress')} />}
    <small className="warm-installation-scan-note">{copy('仅检查安装入口；不读取文档，不代表 COMSOL Bridge 或科学验证通过。', 'Installation detection only; no documents are read and Bridge/scientific validation are not certified.')}</small>
  </div>
}
