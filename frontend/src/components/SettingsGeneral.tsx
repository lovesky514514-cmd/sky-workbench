import type { Locale } from '../i18n'
import { scientificAdapterSummary } from '../installationStatus'
import type { Provider } from '../types'
import { InstallationScanProgress } from './InstallationScanProgress'

type Adapter = {
  engine: string
  installed: boolean
  readback: string
  execute: string
  detail: string
  probe: string
}

type Props = {
  locale: Locale
  providers: Provider[]
  activeProvider: string
  comsolRoot: string
  scan: Record<string, unknown>
  adapters: Adapter[]
  probes: Record<string, { status: string; elapsed_ms: number }>
  working: string
  onRescan: () => void
  onLocaleChange: (locale: Locale) => void
  onProviderSettings: () => void
  onComsolRootChange: (root: string) => void
  onOpenSetup: () => void
  onProbe: (engine: string) => void
}

function probeStatus(status: string, copy: (cn: string, en: string) => string): string {
  const labels: Record<string, string> = {
    LICENSE_OR_ACTIVATION_REQUIRED: copy('本机许可证或引擎激活未就绪', 'Local license or engine activation is not ready'),
    TIMED_OUT: copy('连接超时', 'Connection timed out'),
    PROCESS_CLEANUP_UNVERIFIED: copy('子进程回收未证实', 'Child-process cleanup unverified'),
    HANDSHAKE_FAILED: copy('本机引擎握手失败', 'Local engine handshake failed'),
    CONNECTOR_UNAVAILABLE: copy('缺少此版本的安全连接器', 'No safe connector for this version'),
    PROBE_UNAVAILABLE: copy('连接测试不可用', 'Connection test unavailable'),
  }
  return labels[status] ?? status
}

export function SettingsGeneral(props: Props) {
  const { locale, providers, activeProvider, comsolRoot, scan, adapters, probes, working } = props
  const zh = locale === 'zh-CN'
  const copy = (cn: string, en: string) => zh ? cn : en
  const activeName = providers.find((provider) => provider.name === activeProvider)?.label

  return <>
    <div className="warm-settings-section">
      <div className="warm-settings-row">
        <div>
          <strong>{copy('自动发现本机软件', 'Detect installed software')}</strong>
          <span>{copy('依次检查 COMSOL、MATLAB、Wolfram、Materials Studio 和 Ollama 的安装入口；不读取用户文档。',
            'Checks COMSOL, MATLAB, Wolfram, Materials Studio and Ollama installation entries in order; does not read user documents.')}</span>
        </div>
        <button type="button" onClick={props.onRescan} disabled={scan.state === 'running'}>
          {copy('重新扫描', 'Scan again')}
        </button>
      </div>
      <InstallationScanProgress locale={locale} scan={scan} />
      {scan.state === 'failed' && <p className="warm-settings-scan-result is-warning" role="alert">
        {copy('本机软件检查失败；请重试或查看 Runtime 日志。', 'Local software check failed; retry or inspect Runtime logs.')}
      </p>}
    </div>

    <div className="warm-settings-section">
      <h3>{copy('工作区', 'Workspace')}</h3>
      <div className="warm-settings-row">
        <div><strong>{copy('界面语言', 'Language')}</strong>
          <span>{copy('切换后立即应用，并在下次启动时保留。', 'Applies immediately and persists across launches.')}</span></div>
        <select value={locale} onChange={(event) => props.onLocaleChange(event.target.value as Locale)}>
          <option value="zh-CN">中文</option><option value="en-US">English</option>
        </select>
      </div>
      <div className="warm-settings-row">
        <div><strong>{copy('当前 AI 连接', 'Current AI connection')}</strong>
          <span>{activeName ?? copy('自动选择本地模型', 'Choose a local model automatically')}</span></div>
        <button type="button" onClick={props.onProviderSettings}>{copy('管理连接', 'Manage connection')}</button>
      </div>
      <div className="warm-settings-row">
        <div><strong>{copy('COMSOL 安装目录', 'COMSOL installation root')}</strong>
          <span>{copy('多版本电脑可指定安装根目录；每个版本的 Bridge 与科学工作流必须单独验收。',
            'On multi-version machines select an installation root; Bridge and scientific workflows require separate acceptance for each version.')}</span></div>
        <input aria-label={copy('COMSOL 安装目录', 'COMSOL installation root')}
          value={comsolRoot} onChange={(event) => props.onComsolRootChange(event.target.value)}
          placeholder={copy('留空自动发现', 'Leave blank for automatic detection')} />
      </div>
      <div className="warm-settings-row">
        <div><strong>{copy('环境引导', 'Environment guide')}</strong>
          <span>{copy('重新查看 COMSOL、运行时和本地模型状态。', 'Review COMSOL, runtime and local model status again.')}</span></div>
        <button type="button" onClick={props.onOpenSetup}>{copy('打开引导', 'Open guide')}</button>
      </div>
      <p className="warm-settings-note">{copy('AI 连接、软件检测和科学认证是三种不同状态。',
        'AI connectivity, software discovery and scientific certification are separate states.')}</p>
    </div>

    <div className="warm-settings-section">
      <span className="warm-settings-eyebrow">SCIENTIFIC SOFTWARE</span>
      <h3>{copy('软件适配状态', 'Scientific software adapters')}</h3>
      <p>{copy('“已检测到”只表示本机存在入口；模型读回与执行必须分别验收。',
        'Detected means an executable exists; readback and execution require separate acceptance.')}</p>
      {adapters.length === 0 && <p>{copy('适配状态不可用', 'Adapter status unavailable')}</p>}
      {adapters.map((adapter) => {
        const probe = probes[adapter.engine]
        const description = probe?.status === 'HANDSHAKE_PASSED'
          ? copy('本机引擎握手通过；模型读回和科学执行仍未验证。',
            'Local engine handshake passed; model readback and scientific execution remain unverified.')
          : probe
            ? `${copy('连接测试', 'Connection test')}: ${probeStatus(probe.status, copy)}`
            : scientificAdapterSummary(adapter.installed, adapter.engine === 'COMSOL', zh)
        return <div className="warm-settings-row" key={adapter.engine}>
          <div><strong>{adapter.engine}</strong><span>{description}</span></div>
          {adapter.probe === 'AVAILABLE_ON_REQUEST'
            ? <button type="button" disabled={Boolean(working)} onClick={() => props.onProbe(adapter.engine)}>
              {working === `probe-${adapter.engine}` ? copy('正在连接…', 'Connecting…') : copy('测试本机连接', 'Test local connection')}
            </button>
            : <span>{adapter.installed
              ? copy('已检测到 · 执行未验证', 'Detected · execution unverified') : copy('不可用', 'Unavailable')}</span>}
        </div>
      })}
    </div>
  </>
}
