import { translate, type Locale } from '../i18n'

type ModelTier = 'qwen3:8b' | 'qwen3:4b'

type Props = {
  locale: Locale
  models: Record<string, unknown>
  working: string
  onRefresh: () => void
  onStart: () => void
  onPull: (model: ModelTier) => void
  onCancelPull: () => void
}

export function LocalModelSettings({ locale, models, working, onRefresh, onStart, onPull, onCancelPull }: Props) {
  const copy = (cn: string, en: string) => locale === 'zh-CN' ? cn : en
  const t = (value: string) => translate(locale, value)
  const ollama = (models.ollama ?? {}) as Record<string, unknown>
  const semantic = (models.semantic ?? {}) as Record<string, unknown>
  const hardware = (ollama.hardware ?? {}) as Record<string, unknown>
  const gpu = (hardware.gpu ?? {}) as Record<string, unknown>
  const pullState = (ollama.pull ?? {}) as Record<string, unknown>
  const serviceReady = ollama.state === 'Ready' || ollama.state === 'No Model'
  const installedModels = Array.isArray(ollama.models) ? ollama.models as Array<Record<string, unknown>> : []
  const modelInstalled = (name: string) => installedModels.some((item) => String(item.name ?? item.model ?? '') === name)
  const serviceStopped = ollama.state === 'Installed but Stopped'
  const serviceLabel = serviceReady
    ? copy('服务已连接', 'Service connected')
    : serviceStopped
      ? copy('已安装，服务未启动', 'Installed, service stopped')
      : ollama.state === 'Not Installed'
        ? copy('未安装或未发现', 'Not installed or not found')
        : copy('正在检测', 'Detecting')

  return <div className="warm-settings-section warm-model-center">
    <span className="warm-settings-eyebrow">LOCAL AI · OPTIONAL</span>
    <h3>{copy('本地模型', 'Local models')}</h3>
    <p>{copy('先确认服务，再选择模型。没有 Ollama 不妨碍打开工作台；云端 API 可在另一栏单独配置。',
      'Check the service first, then choose a model. Ollama is optional; cloud APIs are configured separately.')}</p>
    <div className="warm-model-status" role="status">
      <div>
        <span className={`warm-model-status-dot ${serviceReady ? 'is-ready' : ''}`} />
        <strong>Ollama</strong>
        <span>{serviceLabel}</span>
      </div>
      <button type="button" onClick={onRefresh} disabled={Boolean(working)}>{copy('重新检测', 'Detect again')}</button>
    </div>

    {!serviceReady && <div className="warm-model-help">
      <strong>{serviceStopped ? copy('启动本地服务', 'Start local service') : copy('需要本地模型？', 'Need a local model?')}</strong>
      <p>{serviceStopped
        ? copy('Ollama 已安装，但目前无法连接。启动后再次检测；不要把 HTTP 错误当作模型可用。',
          'Ollama is installed but unreachable. Start it and detect again; an HTTP error is not model availability.')
        : copy('先安装 Ollama，再返回此处重新检测。下载按钮在服务可用前保持禁用。',
          'Install Ollama, then detect again here. Model downloads stay disabled until the service is ready.')}</p>
      {serviceStopped
        ? <button type="button" onClick={onStart} disabled={Boolean(working)}>
            {working === 'ollama' ? copy('启动中…', 'Starting…') : copy('启动 Ollama', 'Start Ollama')}
          </button>
        : <a href="https://ollama.com/download" target="_blank" rel="noreferrer">
            {copy('打开 Ollama 官方下载页 ↗', 'Open official Ollama download ↗')}
          </a>}
    </div>}

    <div className="warm-model-tiers">
      {(['qwen3:8b', 'qwen3:4b'] as const).map((model) => <div className="warm-model-tier" key={model}>
        <div>
          <strong>{model}</strong>
          <span>{model === 'qwen3:8b'
            ? copy('标准模式 · 更高显存需求', 'Standard mode · higher VRAM')
            : copy('低显存模式 · 需明确选择', 'Low-VRAM mode · explicit choice')}</span>
        </div>
        <span className={modelInstalled(model) ? 'is-installed' : ''}>
          {modelInstalled(model) ? copy('已安装', 'Installed') : copy('未安装', 'Not installed')}
        </span>
        <button type="button" onClick={() => onPull(model)}
          disabled={!serviceReady || Boolean(working) || pullState.state === 'running' || modelInstalled(model)}>
          {copy('下载…', 'Download…')}
        </button>
      </div>)}
    </div>

    {pullState.state === 'running' && <div className="warm-model-progress" role="status">
      <div>
        <strong>{String(pullState.model ?? '')} · {copy('正在下载', 'Downloading')}</strong>
        <button type="button" onClick={onCancelPull}>{copy('取消', 'Cancel')}</button>
      </div>
      <progress value={typeof pullState.percent === 'number' ? Number(pullState.percent) : undefined}
        max={100} aria-label={copy('当前文件层传输进度', 'Current layer transfer progress')} />
      <small>{typeof pullState.percent === 'number'
        ? copy(`当前文件层 ${pullState.percent}%`, `Current layer ${pullState.percent}%`)
        : copy('Ollama 暂未报告字节进度', 'Ollama has not reported byte progress yet')}
        {' · '}{String(pullState.detail ?? '')}</small>
    </div>}
    {['completed', 'failed', 'cancelled'].includes(String(pullState.state)) &&
      <p className="warm-model-outcome" role="status">{String(pullState.detail ?? pullState.state)}</p>}
    <p className="warm-model-storage">
      {copy('模型存储位置', 'Model storage')}: {String(ollama.model_storage ?? '—')}
      {' · '}{copy('本机 RAM', 'System RAM')} {formatBytes(Number(hardware.ram_bytes ?? 0))}
      {' · '}VRAM {formatBytes(Number(gpu.vram_bytes ?? 0))}
    </p>
    <div className="warm-model-semantic">
      <strong>{t('Semantic Search')}</strong>
      <span>{t('Embedding')}: {t(String(semantic.embedding ?? 'Unavailable'))}
        {' · '}{t('Reranker')}: {t(String(semantic.reranker ?? 'Unavailable'))}</span>
    </div>
  </div>
}

function formatBytes(value: number): string {
  if (!value) return '—'
  const units = ['B', 'KiB', 'MiB', 'GiB', 'TiB']
  let size = value
  let index = 0
  while (size >= 1024 && index < units.length - 1) {
    size /= 1024
    index += 1
  }
  return `${size.toFixed(1)} ${units[index]}`
}
