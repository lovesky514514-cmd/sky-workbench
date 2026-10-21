import { useEffect, useState } from 'react'
import { api, type DesktopBootstrap } from '../api'
import type { EnvironmentItem, Provider } from '../types'
import type { Locale } from '../i18n'
import { BrandIcon } from './BrandIcon'
import { safeUiError } from '../safeUiError'

// A new product-shell key makes the approved Warm Studio guide visible once
// even when a historical RC4.1 guide was dismissed on the same Tauri origin.
export const warmOnboardingStorageKey = 'warm-studio-onboarding-v2'

type Props = {
  locale: Locale
  environment: EnvironmentItem[]
  providers: Provider[]
  desktop: DesktopBootstrap | null
  runtimeReady: boolean
  runtimeUnavailable: boolean
  onComplete: () => void
  onRetryRuntime: () => void
  onOpenLogs: () => void
  onOpenHelp: () => void
}

// The setup surface reads capability status but never interprets a missing
// response as a successful installation or starts a model download itself.
export function WarmOnboarding(props: Props) {
  const zh = props.locale === 'zh-CN'
  const copy = (chinese: string, english: string) => zh ? chinese : english
  const [step, setStep] = useState(0)
  const [models, setModels] = useState<Record<string, unknown>>({})
  const [notice, setNotice] = useState('')
  const labels = [copy('欢迎', 'Welcome'), copy('运行环境', 'Environment'), copy('本地 AI', 'Local AI'), copy('开始使用', 'Ready')]
  const comsol = props.environment.find((item) => item.name.toLowerCase().includes('comsol'))
  const providerReady = props.providers.some((item) => item.status === 'Ready')
  const modelReady = props.desktop?.modelState === 'Detected'
  const ollama = (models.ollama ?? models) as Record<string, unknown>
  const overall = !props.runtimeReady ? 'BLOCKED' : comsol?.status === 'Ready' && providerReady && modelReady ? 'READY' : 'DEGRADED'

  // Model discovery is read-only and begins only after the scientific runtime
  // is reachable; the welcome screen itself renders immediately at app launch.
  useEffect(() => {
    if (!props.runtimeReady) return
    void api.modelsStatus().then(setModels).catch((cause: unknown) => setNotice(String(cause)))
  }, [props.runtimeReady])

  const refreshModels = async () => {
    try { setModels(await api.modelsStatus()); setNotice('') }
    catch (cause) { setNotice(String(cause)) }
  }

  // A model pull remains an explicit, separately confirmed user action. The
  // storage path comes from the runtime and is never silently substituted.
  const requestModel = async (model: 'qwen3:8b' | 'qwen3:4b') => {
    try {
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
          <div className="warm-onboarding-checks"><div><span>Scientific Runtime</span><strong className={props.runtimeReady ? 'is-ready' : 'is-unavailable'}>{props.runtimeReady ? 'READY' : props.runtimeUnavailable ? 'BLOCKED' : copy('检测中', 'CHECKING')}</strong><small>{props.runtimeUnavailable ? copy('运行时无法连接，可重试或查看日志。', 'Runtime unavailable; retry or inspect logs.') : copy('负责科学计划、验证与运行记录。', 'Owns plans, validation and run records.')}</small></div><div><span>COMSOL 6.3</span><strong className={comsol?.status === 'Ready' ? 'is-ready' : 'is-unavailable'}>{comsol?.status === 'Ready' ? 'READY' : props.runtimeReady ? 'UNAVAILABLE' : copy('检测中', 'CHECKING')}</strong><small>{comsol?.detail ?? copy('等待本机环境检测。', 'Waiting for local detection.')}</small></div></div>
          <div className="warm-onboarding-inline-actions"><button type="button" onClick={props.onRetryRuntime} disabled={!props.runtimeUnavailable}>{copy('重试运行时', 'Retry runtime')}</button><button type="button" onClick={props.onOpenLogs}>{copy('打开日志', 'Open logs')}</button><button type="button" onClick={props.onOpenHelp}>{copy('故障帮助', 'Troubleshooting help')}</button></div>
          <p className="warm-onboarding-honesty">{copy('未连接 COMSOL 时可浏览工作台，但真实 RUN 不可用。', 'You can inspect the workspace without COMSOL, but real RUN is unavailable.')}</p>
        </section>}
        {step === 2 && <section className="warm-onboarding-page">
          <span className="warm-onboarding-kicker">03 / 04 · {copy('本地 AI', 'LOCAL AI')}</span>
          <h1>{copy('连接本地模型。', 'Connect a local model.')}</h1>
          <p className="warm-onboarding-lead">{copy('Ollama 与模型按实际可用状态显示；缺失时不自动下载、不静默降级。', 'Ollama and models show their actual availability; nothing downloads or falls back silently.')}</p>
          <div className="warm-onboarding-checks"><div><span>Ollama</span><strong className={String(ollama.state) === 'Ready' ? 'is-ready' : 'is-unavailable'}>{String(ollama.state ?? (props.runtimeReady ? 'UNAVAILABLE' : 'CHECKING')).toUpperCase()}</strong><small>{String(ollama.detail ?? copy('等待本地模型服务检测。', 'Waiting for local model service detection.'))}</small></div><div><span>{props.desktop?.selectedModel ?? 'qwen3:8b'}</span><strong className={modelReady ? 'is-ready' : 'is-unavailable'}>{modelReady ? 'READY' : 'UNAVAILABLE'}</strong><small>{copy('模型目录', 'Model storage')}: {String(ollama.model_storage ?? props.desktop?.ollamaModelsRoot ?? '—')}</small></div></div>
          <div className="warm-onboarding-inline-actions"><button type="button" onClick={() => void refreshModels()} disabled={!props.runtimeReady}>{copy('重新检测', 'Detect again')}</button><button type="button" onClick={() => void api.startOllama().then(refreshModels).catch((cause: unknown) => setNotice(String(cause)))} disabled={!props.runtimeReady || ollama.state === 'Not Installed'}>{copy('启动 Ollama', 'Start Ollama')}</button><button type="button" onClick={() => void requestModel('qwen3:8b')} disabled={!props.runtimeReady}>{copy('下载 8B…', 'Download 8B…')}</button><button type="button" onClick={() => void requestModel('qwen3:4b')} disabled={!props.runtimeReady}>{copy('下载 4B…', 'Download 4B…')}</button></div>
          <p className="warm-onboarding-honesty">{copy('模型下载需要再次确认；现有 OLLAMA_MODELS 路径保持不变。', 'Model downloads require another confirmation; the existing OLLAMA_MODELS path is preserved.')}</p>
        </section>}
        {step === 3 && <section className="warm-onboarding-page">
          <span className="warm-onboarding-kicker">04 / 04 · {copy('开始使用', 'READY')}</span>
          <h1>{copy('工作台已为你准备好。', 'Your workspace is prepared.')}</h1>
          <p className="warm-onboarding-lead">{copy('你可以随时在右上角菜单重新打开引导。缺失能力会保持明确的 unavailable 状态。', 'Reopen this guide from the top-right menu at any time. Missing capabilities remain explicitly unavailable.')}</p>
          <div className="warm-onboarding-verdict"><span>{copy('当前产品状态', 'CURRENT PRODUCT STATE')}</span><strong className={overall === 'READY' ? 'is-ready' : 'is-unavailable'}>{overall}</strong><p>{overall === 'READY' ? copy('环境检测就绪；运行仍需逐项审查科学计划。', 'Environment detected; every RUN still requires scientific plan review.') : overall === 'BLOCKED' ? copy('Scientific Runtime 尚未就绪，不能声明可运行。', 'Scientific Runtime is not ready; execution cannot be claimed.') : copy('工作区可打开，但缺失的 COMSOL 或模型能力不可用。', 'The workspace can open, but missing COMSOL or model capabilities are unavailable.')}</p></div>
          <button type="button" className="warm-onboarding-primary warm-onboarding-open" onClick={props.onComplete}>{copy('打开科学工作区 →', 'Open scientific workspace →')}</button>
        </section>}
        {notice && <p className="warm-onboarding-notice" role="status">{safeUiError(notice)}</p>}
        <footer className="warm-onboarding-footer"><span>{copy('科学结论必须有真实、可验证的证据。', 'Scientific claims require real, verifiable evidence.')}</span><div><button type="button" onClick={() => setStep((value) => Math.max(0, value - 1))} disabled={step === 0}>{copy('上一步', 'Back')}</button>{step < 3 && <button type="button" className="warm-onboarding-primary" onClick={() => setStep((value) => Math.min(3, value + 1))}>{copy('继续 →', 'Continue →')}</button>}</div></footer>
      </main>
    </div>
  </div>
}
