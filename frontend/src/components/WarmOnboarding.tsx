import { useEffect, useState } from 'react'
import { api, type DesktopBootstrap } from '../api'
import type { EnvironmentItem, Provider } from '../types'
import type { Locale } from '../i18n'
import { BrandIcon } from './BrandIcon'
import { safeUiError } from '../safeUiError'
import { InstallationScanProgress } from './InstallationScanProgress'
import { detectedScientificEngines, environmentReadiness, verifiedProviderReady } from '../installationStatus'

// Reopen the guide once when its setup flow changes.
export const warmOnboardingStorageKey = 'warm-studio-onboarding-v3'

type Props = {
  locale: Locale
  environment: EnvironmentItem[]
  providers: Provider[]
  activeProvider: string
  desktop: DesktopBootstrap | null
  runtimeReady: boolean
  runtimeUnavailable: boolean
  onComplete: () => void
  onRetryRuntime: () => void
  onOpenLogs: () => void
  onOpenHelp: () => void
  onOpenProviderSettings?: () => void
  onInstallationScanned?: (result: Record<string, unknown>) => void
}

// The setup surface reads capability status but never interprets a missing
// response as a successful installation or starts a model download itself.
export function WarmOnboarding(props: Props) {
  const zh = props.locale === 'zh-CN'
  const copy = (chinese: string, english: string) => zh ? chinese : english
  const [step, setStep] = useState(0)
  const [models, setModels] = useState<Record<string, unknown>>({})
  const [scan, setScan] = useState<Record<string, unknown>>({ state: 'idle' })
  const [notice, setNotice] = useState('')
  const labels = [copy('欢迎', 'Welcome'), copy('运行环境', 'Environment'), copy('AI 连接', 'AI connection'), copy('开始使用', 'Ready')]
  const comsol = props.environment.find((item) => item.name.toLowerCase().includes('comsol'))
  const scannedComsol = scan.state === 'completed' && scan.comsol && typeof scan.comsol === 'object' ? scan.comsol as Record<string, unknown> : null
  const comsolDetected = scannedComsol ? scannedComsol.found === true : comsol?.status === 'Ready'
  const comsolLabel = String(scannedComsol?.version ?? comsol?.detail.match(/COMSOL 6\.\d+/)?.[0] ?? 'COMSOL')
  const activeAi = props.providers.find((provider) => provider.name === props.activeProvider)
  const providerReady = verifiedProviderReady(props.providers, props.activeProvider)
  const modelReady = props.desktop?.modelState === 'Detected'
  const ollama = (models.ollama ?? models) as Record<string, unknown>
  const ollamaServiceReady = ollama.state === 'Ready' || ollama.state === 'No Model'
  const pullState = (ollama.pull ?? {}) as Record<string, unknown>
  const overall = environmentReadiness(props.runtimeReady, comsolDetected, providerReady)
  const detectedEngines = scan.state === 'completed' ? detectedScientificEngines(scan) : []

  // Model discovery is read-only and begins only after the scientific runtime
  // is reachable; the welcome screen itself renders immediately at app launch.
  useEffect(() => {
    if (!props.runtimeReady) return
    void api.modelsStatus().then(setModels).catch((cause: unknown) => setNotice(String(cause)))
  }, [props.runtimeReady])

  // Start a bounded installation scan when the user reaches Environment.
  // The bar uses inspected candidate counts returned by Runtime, not elapsed
  // time or an invented disk-scan percentage.
  useEffect(() => {
    if (step !== 1 || !props.runtimeReady || scan.state !== 'idle') return
    void api.startInstallationScan().then(setScan).catch(() => setScan({ state: 'failed' }))
  }, [step, props.runtimeReady, scan.state])
  useEffect(() => {
    if (scan.state !== 'running') return
    const timer = window.setInterval(() => {
      void api.installationScanStatus().then((result) => { setScan(result); if (result.state === 'completed') props.onInstallationScanned?.(result) }).catch(() => setScan({ state: 'failed' }))
    }, 350)
    return () => window.clearInterval(timer)
  }, [scan.state])
  useEffect(() => {
    if (pullState.state !== 'running') return
    const timer = window.setInterval(() => { void api.modelsStatus().then(setModels).catch(() => undefined) }, 500)
    return () => window.clearInterval(timer)
  }, [pullState.state])

  const refreshModels = async () => {
    try { setModels(await api.modelsStatus()); setNotice('') }
    catch (cause) { setNotice(String(cause)) }
  }

  // A model pull remains an explicit, separately confirmed user action. The
  // storage path comes from the runtime and is never silently substituted.
  const requestModel = async (model: 'qwen3:8b' | 'qwen3:4b') => {
    try {
      if (!ollamaServiceReady) {
        setNotice(copy('Ollama 服务不可用；请安装或启动后重新检测。', 'Ollama is unavailable; install or start it and detect again.'))
        return
      }
      const preview = await api.pullOllama(model, false)
      const destination = String(ollama.model_storage ?? props.desktop?.ollamaModelsRoot ?? 'Ollama model storage')
      const description = String(preview.detail ?? `${model} → ${destination}`)
      if (!window.confirm(`${description}\n\n${copy('确认开始大型模型下载？', 'Start this large model download?')}`)) return
      await api.pullOllama(model, true)
      await refreshModels()
      setNotice(copy(`${model} 下载已开始；请在模型中心查看进度。`, `${model} download started; check Model Center for progress.`))
    } catch (cause) { setNotice(String(cause)) }
  }

  return <div className="warm-onboarding" role="dialog" aria-modal="true" aria-label={copy('首次使用引导', 'First-run guide')}>
    <header className="warm-onboarding-header">
      <div className="warm-onboarding-brand"><BrandIcon size={32} /><strong>SKY</strong><span>Workbench</span></div>
      <span>{copy('首次使用 · 科学工作区', 'First run · scientific workspace')}</span>
      <button type="button" onClick={props.onComplete}>{copy('稍后设置', 'Set up later')}</button>
    </header>
    <div className="warm-onboarding-body">
      <nav className="warm-onboarding-steps" aria-label={copy('引导步骤', 'Setup steps')}>
        <span className="warm-onboarding-nav-title">{copy('开始使用', 'GETTING STARTED')}</span>
        {labels.map((label, index) => <button key={label} type="button" className={step === index ? 'is-active' : ''} aria-current={step === index ? 'step' : undefined} onClick={() => setStep(index)}><span>{String(index + 1).padStart(2, '0')}</span>{label}</button>)}
        <p>{copy('先确认环境，再开始建模。所有科学结论仍以真实运行证据为准。', 'Check the environment before modelling. Scientific claims still require real execution evidence.')}</p>
      </nav>
      <main className="warm-onboarding-main">
        {step === 0 && <section className="warm-onboarding-page warm-onboarding-welcome">
          <span className="warm-onboarding-kicker">01 / 04 · {copy('欢迎', 'WELCOME')}</span>
          <h1>{copy('从科学问题开始。', 'Begin with a scientific question.')}</h1>
          <p className="warm-onboarding-lead">{copy('在同一工作区中制定计划、审查模型、运行求解，并查看可追溯的证据。', 'Plan, inspect the model, run a solve and review traceable evidence in one workspace.')}</p>
          <div className="warm-onboarding-sequence"><div><b>01</b><strong>{copy('描述问题', 'Describe')}</strong><span>{copy('几何、材料、边界条件和期望结果', 'Geometry, materials, boundaries and desired result')}</span></div><div><b>02</b><strong>{copy('审查计划', 'Review')}</strong><span>{copy('运行前确认假设和模型对象', 'Confirm assumptions and model objects before RUN')}</span></div><div><b>03</b><strong>{copy('验证证据', 'Verify')}</strong><span>{copy('成功命令不等于科学验证通过', 'Command success is not scientific validation')}</span></div></div>
          <p className="warm-onboarding-honesty">{copy('这是独立工作台；真实求解仍需要有效的 COMSOL 安装与许可证。', 'This is an independent workbench; real solves still require a valid COMSOL installation and license.')}</p>
        </section>}
        {step === 1 && <section className="warm-onboarding-page">
          <span className="warm-onboarding-kicker">02 / 04 · {copy('运行环境', 'ENVIRONMENT')}</span>
          <h1>{copy('确认计算环境。', 'Check the compute environment.')}</h1>
          <p className="warm-onboarding-lead">{copy('状态来自本机检测。尚未检测到的能力不会标记为可用。', 'Statuses come from local detection. Undetected capabilities are never shown as available.')}</p>
          <div className="warm-onboarding-checks"><div><span>Scientific Runtime</span><strong className={props.runtimeReady ? 'is-ready' : 'is-unavailable'}>{props.runtimeReady ? 'READY' : props.runtimeUnavailable ? 'BLOCKED' : copy('检测中', 'CHECKING')}</strong><small>{props.runtimeUnavailable ? copy('运行时无法连接，可重试或查看日志。', 'Runtime unavailable; retry or inspect logs.') : copy('负责科学计划、验证与运行记录。', 'Owns plans, validation and run records.')}</small></div><div><span>{comsolLabel}</span><strong className={comsolDetected ? 'is-ready' : 'is-unavailable'}>{comsolDetected ? 'DETECTED' : props.runtimeReady ? 'UNAVAILABLE' : copy('检测中', 'CHECKING')}</strong><small>{comsolDetected ? copy('只确认安装；Java Bridge 与科学运行尚未验证。', 'Installation only; Java Bridge and scientific execution remain unverified.') : comsol?.detail ?? copy('等待本机环境检测。', 'Waiting for local detection.')}</small></div></div>
          <InstallationScanProgress locale={props.locale} scan={scan} />
          {scan.state === 'completed' && <p className="warm-onboarding-scan-result" role="status">{copy('安装位置检查完成', 'Installation check complete')} · {String(((scan.comsol ?? {}) as Record<string, unknown>).version ?? 'COMSOL')} {((scan.comsol ?? {}) as Record<string, unknown>).found ? copy('已发现，连接未验证', 'found, connection unverified') : copy('未发现', 'not found')} · Ollama {scan.ollama_executable ? copy('已发现', 'found') : copy('未发现', 'not found')}</p>}
          {scan.state === 'completed' && <p className="warm-onboarding-scan-result" role="status">{copy('其他科学软件', 'Other scientific software')}: {detectedEngines.length ? detectedEngines.join(' · ') : copy('未检测到已支持的安装入口', 'No supported installation entry detected')} · {copy('连接和科学执行均未验证', 'connection and scientific execution unverified')}</p>}
          {scan.state === 'failed' && <p className="warm-onboarding-scan-result is-warning" role="alert">{copy('安装位置扫描失败；可在设置中重试。', 'Installation scan failed; retry from Settings.')}</p>}
          <div className="warm-onboarding-inline-actions"><button type="button" onClick={props.onRetryRuntime} disabled={!props.runtimeUnavailable}>{copy('重试运行时', 'Retry runtime')}</button><button type="button" onClick={props.onOpenLogs}>{copy('打开日志', 'Open logs')}</button><button type="button" onClick={props.onOpenHelp}>{copy('故障帮助', 'Troubleshooting help')}</button></div>
          <p className="warm-onboarding-honesty">{copy('未连接 COMSOL 时可浏览工作台，但真实 RUN 不可用。', 'You can inspect the workspace without COMSOL, but real RUN is unavailable.')}</p>
        </section>}
        {step === 2 && <section className="warm-onboarding-page">
          <span className="warm-onboarding-kicker">03 / 04 · {copy('AI 连接', 'AI CONNECTION')}</span>
          <h1>{copy('选择 AI 连接。', 'Choose an AI connection.')}</h1>
          <p className="warm-onboarding-lead">{copy('本地模型和云端 API 都可选。选择一种连接，测试后再使用。', 'Local models and cloud APIs are optional. Choose one connection and test it before use.')}</p>
          <div className="warm-onboarding-checks"><div><span>{activeAi?.label ?? copy('本地自动选择', 'Automatic local selection')}</span><strong className={providerReady ? 'is-ready' : 'is-unavailable'}>{providerReady ? 'READY' : 'UNVERIFIED'}</strong><small>{copy('尚无真实连接证据时不标记就绪。', 'Not ready without a real connection check.')}</small></div></div>
          {pullState.state === 'running' && <div className="warm-onboarding-scan" role="status"><strong>{String(pullState.model ?? '')} · {copy('正在下载', 'Downloading')}</strong><progress value={typeof pullState.percent === 'number' ? Number(pullState.percent) : undefined} max={100} aria-label={copy('当前文件层下载进度', 'Current layer download progress')} /><small>{typeof pullState.percent === 'number' ? `${pullState.percent}% · ${String(pullState.detail ?? '')}` : copy('正在等待 Ollama 报告字节进度', 'Waiting for byte progress from Ollama')}</small></div>}
          <div className="warm-onboarding-api-guide"><strong>{copy('云端 API', 'Cloud API')}</strong><p>{copy('Kimi、DeepSeek、豆包；其他服务在设置中折叠。密钥不会回显或写入普通设置。', 'Kimi, DeepSeek and Doubao; other services stay collapsed. Keys are not echoed or saved in ordinary settings.')}</p><button type="button" onClick={props.onOpenProviderSettings} disabled={!props.runtimeReady || !props.onOpenProviderSettings}>{copy('配置 API →', 'Configure API →')}</button></div>
          <details className="warm-onboarding-local-options"><summary>{copy('使用本地 Ollama', 'Use local Ollama')}</summary><div className="warm-onboarding-checks"><div><span>Ollama</span><strong className={ollamaServiceReady ? 'is-ready' : 'is-unavailable'}>{ollamaServiceReady ? 'READY' : 'UNAVAILABLE'}</strong><small>{modelReady ? copy('选定模型已检测', 'Selected model detected') : copy('选定模型不可用', 'Selected model unavailable')}</small></div></div>{ollama.state === 'Not Installed' && <a href="https://ollama.com/download" target="_blank" rel="noreferrer">{copy('官方下载', 'Official download')}</a>}<div className="warm-onboarding-inline-actions"><button type="button" onClick={() => void refreshModels()} disabled={!props.runtimeReady}>{copy('重新检测', 'Detect again')}</button><button type="button" onClick={() => void api.startOllama().then(refreshModels).catch((cause: unknown) => setNotice(safeUiError(cause)))} disabled={!props.runtimeReady || ollama.state === 'Not Installed'}>{copy('启动 Ollama', 'Start Ollama')}</button><button type="button" onClick={() => void requestModel('qwen3:8b')} disabled={!ollamaServiceReady || pullState.state === 'running'}>{copy('下载 8B…', 'Download 8B…')}</button><button type="button" onClick={() => void requestModel('qwen3:4b')} disabled={!ollamaServiceReady || pullState.state === 'running'}>{copy('下载 4B…', 'Download 4B…')}</button></div></details>
        </section>}
        {step === 3 && <section className="warm-onboarding-page">
          <span className="warm-onboarding-kicker">04 / 04 · {copy('开始使用', 'READY')}</span>
          <h1>{copy('工作台已为你准备好。', 'Your workspace is prepared.')}</h1>
          <p className="warm-onboarding-lead">{copy('你可以随时在右上角菜单重新打开引导。缺失能力会保持明确的 unavailable 状态。', 'Reopen this guide from the top-right menu at any time. Missing capabilities remain explicitly unavailable.')}</p>
          <div className="warm-onboarding-verdict"><span>{copy('当前产品状态', 'CURRENT PRODUCT STATE')}</span><strong className={overall === 'READY' ? 'is-ready' : 'is-unavailable'}>{overall}</strong><p>{overall === 'READY' ? copy('环境检测就绪；运行仍需逐项审查科学计划。', 'Environment detected; every RUN still requires scientific plan review.') : overall === 'BLOCKED' ? copy('Scientific Runtime 尚未就绪，不能声明可运行。', 'Scientific Runtime is not ready; execution cannot be claimed.') : copy('工作区可打开；未连接 COMSOL 或尚未实测的 AI 连接不算就绪。', 'The workspace can open; missing COMSOL or an untested AI connection is not ready.')}</p></div>
          <button type="button" className="warm-onboarding-primary warm-onboarding-open" onClick={props.onComplete}>{copy('打开科学工作区 →', 'Open scientific workspace →')}</button>
        </section>}
        {notice && <p className="warm-onboarding-notice" role="status">{safeUiError(notice)}</p>}
        <footer className="warm-onboarding-footer"><span>{copy('科学结论必须有真实、可验证的证据。', 'Scientific claims require real, verifiable evidence.')}</span><div><button type="button" onClick={() => setStep((value) => Math.max(0, value - 1))} disabled={step === 0}>{copy('上一步', 'Back')}</button>{step < 3 && <button type="button" className="warm-onboarding-primary" onClick={() => setStep((value) => Math.min(3, value + 1))}>{copy('继续 →', 'Continue →')}</button>}</div></footer>
      </main>
    </div>
  </div>
}
