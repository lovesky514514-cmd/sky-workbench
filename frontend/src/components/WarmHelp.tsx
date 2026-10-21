import type { DesktopBootstrap } from '../api'
import type { EnvironmentItem } from '../types'
import type { Locale } from '../i18n'

type Props = {
  locale: Locale
  desktop: DesktopBootstrap | null
  environment: EnvironmentItem[]
  onClose: () => void
  onOpenLogs: () => void
}

// Installation guidance is selected from observed local status. It never
// upgrades a detected executable or installed model into a scientific verdict.
export function WarmHelp({ locale, desktop, environment, onClose, onOpenLogs }: Props) {
  const zh = locale === 'zh-CN'
  const copy = (cn: string, en: string) => zh ? cn : en
  const comsol = environment.find((item) => item.name === 'COMSOL')
  const bridge = environment.find((item) => item.name === 'Java Bridge')
  const missingModel = desktop?.modelState !== 'Detected'
  return <div className="warm-help-backdrop" role="presentation" onMouseDown={onClose}>
    <section className="warm-help" role="dialog" aria-modal="true" aria-label={copy('工作台帮助', 'Workbench help')} onMouseDown={(event) => event.stopPropagation()}>
      <header><div><small>{copy('内置帮助', 'IN-APP HELP')}</small><h2>{copy('检查环境与恢复操作', 'Environment and recovery')}</h2></div><button onClick={onClose} aria-label={copy('关闭帮助', 'Close help')}>×</button></header>
      <p>{copy('状态来自本机检测；没有运行证据时不会宣称科学验证通过。', 'Statuses come from local detection; scientific success is never inferred without run evidence.')}</p>
      <div className="warm-help-sections">
        <article><strong>COMSOL / Java Bridge</strong><span>{comsol?.status === 'Ready' ? copy('已发现 COMSOL 程序；仍需有效许可证及模型连接。', 'COMSOL executable detected; a valid license and live model connection are still required.') : copy('未检测到 COMSOL。请检查独立安装、许可证及安装路径，然后在“环境”中重试。', 'COMSOL was not detected. Check its separate installation, license and path, then retry Environment.')}</span><small>{bridge?.status === 'Ready' ? copy('Bridge 已连接。', 'Bridge connected.') : copy('Bridge 未连接；运行前须确认 ModelUtil 与模型读回。', 'Bridge disconnected; ModelUtil and model readback must be confirmed before RUN.')}</small></article>
        <article><strong>Ollama / Qwen</strong><span>{missingModel ? copy('当前选定模型不可用。先启动本机 Ollama，再在引导或设置中重新检测；不自动下载或切换。', 'The selected model is unavailable. Start local Ollama and re-detect it in setup; no automatic download or fallback occurs.') : copy('已检测到选定模型；实际推理和科学计划仍须逐次验证。', 'The selected model was detected; actual inference and each plan still require verification.')}</span><small>{desktop?.selectedModel ?? 'qwen3:8b'}</small></article>
        <article><strong>{copy('工作区与故障恢复', 'Workspace and recovery')}</strong><span>{copy('模型放在 workspace/models；输出与报告分别在 workspace/outputs 和 workspace/reports。旧数据损坏时保留原始备份，历史结果不得自动认证。', 'Models live in workspace/models; outputs and reports live in workspace/outputs and workspace/reports. A damaged history must be preserved and cannot be auto-certified.')}</span><small>{copy('强制停止会结束当前 attempt；只有验证过的安全暂停才能继续。', 'Force stop ends the current attempt; only a verified safe pause is resumable.')}</small></article>
        <article><strong>{copy('修改已有 MPH', 'Modify an existing MPH')}</strong><span>{copy('先保留原件，在副本上读取模型树、提出修改计划并等待确认。修改后必须重新读回、求解和验证；当前候选版尚无完整的通用 MPH 导入修改流程。', 'Keep the original, inspect a copy, review proposed changes, then read back, solve and validate. This candidate does not yet offer a complete general-purpose MPH import-and-edit workflow.')}</span></article>
      </div>
      <footer><button onClick={onOpenLogs}>{copy('打开诊断日志', 'Open diagnostic logs')}</button><button onClick={onClose}>{copy('关闭', 'Close')}</button></footer>
    </section>
  </div>
}
