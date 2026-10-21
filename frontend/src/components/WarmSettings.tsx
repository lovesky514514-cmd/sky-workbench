import { useEffect, useState } from 'react'
import { api } from '../api'
import { translate, type Locale } from '../i18n'
import type { Provider, ProviderAcceptanceResult, ProviderConfig, ProviderSettings, ProviderTestResult, RetrievalMode } from '../types'
import { BrandIcon } from './BrandIcon'

type Section = 'general' | 'models' | 'providers' | 'retrieval' | 'extensions' | 'storage'
type Props = {
  locale: Locale
  providers: Provider[]
  settings: ProviderSettings
  onLocaleChange: (locale: Locale) => void
  onOpenSetup: () => void
  onClose: () => void
  onSaved: () => Promise<void>
  visualFixture?: boolean
}

// Product settings keep the existing API contract. The view only reorganizes
// controls into progressive sections so provider diagnostics are not the first
// thing a scientist sees when opening Settings.
export function WarmSettings({ locale, providers, settings, onLocaleChange, onOpenSetup, onClose, onSaved, visualFixture = false }: Props) {
  const t = (source: string) => translate(locale, source)
  const zh = locale === 'zh-CN'
  const copy = (chinese: string, english: string) => zh ? chinese : english
  const [section, setSection] = useState<Section>('general')
  const [configs, setConfigs] = useState<Record<string, ProviderConfig>>(() => Object.fromEntries(Object.entries(settings.providers).map(([name, config]) => [name, { ...config, api_key: '' }])))
  const [activeProvider, setActiveProvider] = useState(settings.active_provider || '')
  const [retrievalMode, setRetrievalMode] = useState<RetrievalMode>(() => normalizeRetrievalMode(settings.retrieval_mode))
  const [literatureEnabled, setLiteratureEnabled] = useState(Boolean(settings.extensions?.literature_modeling?.enabled))
  const [literatureDeepSeek, setLiteratureDeepSeek] = useState(Boolean(settings.extensions?.literature_modeling?.deepseek_enabled))
  const [deepseekAvailable, setDeepseekAvailable] = useState<boolean | null>(null)
  const [models, setModels] = useState<Record<string, unknown>>({})
  const [tests, setTests] = useState<Record<string, ProviderTestResult>>({})
  const [acceptance, setAcceptance] = useState<Record<string, ProviderAcceptanceResult>>({})
  const [working, setWorking] = useState('')
  const [notice, setNotice] = useState('')
  const ollama = (models.ollama ?? {}) as Record<string, unknown>
  const semantic = (models.semantic ?? {}) as Record<string, unknown>
  const hardware = (ollama.hardware ?? {}) as Record<string, unknown>
  const recommendation = String(ollama.recommendation ?? '')
  const recommended = recommendation === 'Light' ? 'qwen3:4b' : recommendation === 'Balanced' || recommendation === 'Strong' ? 'qwen3:8b' : '—'

  // Read-only discovery may fail when the sidecar is unavailable; failure is
  // displayed explicitly and does not change provider or model selection.
  const refreshModels = async () => {
    try { setModels(await api.modelsStatus()); setNotice('') }
    catch (cause) { setNotice(String(cause)) }
  }
  useEffect(() => { if (!visualFixture) void refreshModels() }, [visualFixture])
  useEffect(() => { if (!visualFixture) void api.literatureModelingStatus().then((status) => setDeepseekAvailable(status.deepseek_available)).catch(() => setDeepseekAvailable(null)) }, [visualFixture])

  // Existing save, connection-test and acceptance endpoints are preserved.
  // Only the presentation and navigation around them have changed.
  const update = (name: string, field: keyof ProviderConfig, value: string | boolean) => setConfigs((current) => ({ ...current, [name]: { ...current[name], [field]: value } }))
  const save = async () => {
    setWorking('save')
    try { await api.saveProviders(configs, retrievalMode, activeProvider); await api.setLiteratureModeling(literatureEnabled); const status = await api.setLiteratureDeepSeek(literatureDeepSeek); setDeepseekAvailable(status.deepseek_available); await onSaved() }
    catch (cause) { setNotice(String(cause)) }
    finally { setWorking('') }
  }
  const test = async (name: string) => {
    setWorking(`test-${name}`)
    try { await api.saveProviders(configs, retrievalMode, activeProvider); const result = await api.testProvider(name); setTests((current) => ({ ...current, [name]: result })) }
    catch (cause) { setNotice(String(cause)) }
    finally { setWorking('') }
  }
  const accept = async (name: string) => {
    setWorking(`accept-${name}`)
    try { await api.saveProviders(configs, retrievalMode, activeProvider); const result = await api.acceptProvider(name); setAcceptance((current) => ({ ...current, [name]: result })) }
    catch (cause) { setNotice(String(cause)) }
    finally { setWorking('') }
  }

  // Starting Ollama and pulling a model are separate user actions. The model
  // choice is limited to the declared 4B/8B tiers, with a download preview.
  const startOllama = async () => {
    setWorking('ollama')
    try { await api.startOllama(); await refreshModels() }
    catch (cause) { setNotice(String(cause)) }
    finally { setWorking('') }
  }
  const pull = async (model: 'qwen3:8b' | 'qwen3:4b') => {
    setWorking(`pull-${model}`)
    try {
      const preview = await api.pullOllama(model, false)
      const message = `${model}\n${String(preview.estimated_download ?? '')}\n${String(preview.storage ?? ollama.model_storage ?? '')}`
      if (!window.confirm(`${t('Large model download confirmation')}\n\n${message}`)) return
      await api.pullOllama(model, true)
      setNotice(copy(`${model} 下载已开始。`, `${model} download started.`))
      setModels(await api.modelsStatus())
    } catch (cause) { setNotice(String(cause)) }
    finally { setWorking('') }
  }

  // Storage cleanup is unchanged and restricted to rebuildable cache. User
  // models, documents and reports are never targets of this control.
  const clearStorage = async (kind: 'temporary-cache' | 'semantic-index') => {
    if (!window.confirm(t('Only rebuildable cache will be removed. Scientific models, documents and reports are preserved.'))) return
    setWorking(kind)
    try { const result = await api.clearStorage(kind); await refreshModels(); setNotice(`${t('Storage cleanup complete')}: ${JSON.stringify(result)}`) }
    catch (cause) { setNotice(String(cause)) }
    finally { setWorking('') }
  }

  const sections: Array<[Section, string]> = [
    ['general', copy('常规', 'General')], ['models', copy('本地模型', 'Local models')],
    ['providers', copy('AI 提供商', 'AI providers')], ['retrieval', copy('检索', 'Retrieval')],
    ['extensions', copy('扩展功能', 'Extensions')],
    ['storage', copy('存储', 'Storage')],
  ]

  return <div className="warm-settings-backdrop" onMouseDown={onClose}>
    <section className="warm-settings" role="dialog" aria-modal="true" aria-label={copy('工作台设置', 'Workbench settings')} onMouseDown={(event) => event.stopPropagation()}>
      <header className="warm-settings-header"><BrandIcon size={32} /><h2>{copy('设置', 'Settings')}</h2><span>SKY Workbench</span><button type="button" onClick={onClose} aria-label={t('Close')}>×</button></header>
      <div className="warm-settings-body"><nav className="warm-settings-nav" aria-label={copy('设置分类', 'Settings sections')}>{sections.map(([key, label]) => <button type="button" key={key} className={section === key ? 'is-active' : ''} aria-current={section === key ? 'page' : undefined} onClick={() => setSection(key)}>{label}</button>)}<button type="button" className="warm-settings-guide" onClick={onOpenSetup}>{copy('重新打开使用引导 ↗', 'Open setup guide ↗')}</button></nav>
        <main className="warm-settings-main">
          {section === 'general' && <div className="warm-settings-section"><span className="warm-settings-eyebrow">WORKSPACE</span><h3>{copy('按你的方式工作。', 'Make the workspace yours.')}</h3><p>{copy('常用选项保持简洁；模型和连接诊断放在需要时再打开的分类中。', 'Keep everyday choices simple; model and connection details are one section away.')}</p><div className="warm-settings-row"><div><strong>{copy('界面语言', 'Language')}</strong><span>{copy('切换后立即应用，并在下次启动时保留。', 'Applies immediately and persists across launches.')}</span></div><select value={locale} onChange={(event) => onLocaleChange(event.target.value as Locale)}><option value="zh-CN">中文</option><option value="en-US">English</option></select></div><div className="warm-settings-row"><div><strong>{copy('当前 AI 提供商', 'Active AI provider')}</strong><span>{copy('自动模式优先使用可用的本地提供商。', 'Automatic mode prefers an available local provider.')}</span></div><select value={activeProvider} onChange={(event) => setActiveProvider(event.target.value)}><option value="">{t('Automatic local-first')}</option>{providers.map((provider) => <option value={provider.name} key={provider.name}>{provider.label}</option>)}</select></div><div className="warm-settings-row"><div><strong>{copy('环境引导', 'Environment guide')}</strong><span>{copy('重新查看 COMSOL、运行时和本地模型状态。', 'Review COMSOL, runtime and local model status again.')}</span></div><button type="button" onClick={onOpenSetup}>{copy('打开引导', 'Open guide')}</button></div><p className="warm-settings-note">{copy('AI 提供商的可用状态来自真实检测；设置选项本身不会证明科学任务可运行。', 'Provider availability comes from real detection; a settings choice alone never proves that a scientific task can run.')}</p></div>}
          {section === 'models' && <div className="warm-settings-section"><span className="warm-settings-eyebrow">MODEL CENTER</span><h3>{copy('本地模型', 'Local models')}</h3><p>{copy('查看 Ollama 和模型实际状态。下载始终需要明确确认。', 'Inspect actual Ollama and model status. Downloads always need explicit confirmation.')}</p><div className="warm-settings-model"><header><strong>Ollama</strong><span>{t(String(ollama.state ?? 'Unavailable'))}</span></header><p>{String(ollama.detail ?? copy('尚无本地服务状态。', 'No local service status yet.'))}</p><p>{copy('模型目录', 'Model storage')}: {String(ollama.model_storage ?? '—')}</p><div className="warm-settings-model-actions"><button type="button" onClick={() => void refreshModels()}>{t('Detect')}</button><button type="button" onClick={() => void startOllama()} disabled={Boolean(working) || ollama.state === 'Not Installed'}>{t('Start Ollama')}</button></div></div><div className="warm-settings-model"><header><strong>{copy('推荐档位', 'Recommended tier')}</strong><span>{recommended}</span></header><p>{copy('硬件报告', 'Hardware report')}: RAM {formatBytes(Number(hardware.ram_bytes ?? 0))} · VRAM {formatBytes(Number(((hardware.gpu ?? {}) as Record<string, unknown>).vram_bytes ?? 0))}</p><div className="warm-settings-model-actions"><button type="button" onClick={() => void pull('qwen3:8b')} disabled={Boolean(working)}>{copy('下载 8B…', 'Download 8B…')}</button><button type="button" onClick={() => void pull('qwen3:4b')} disabled={Boolean(working)}>{copy('下载 4B…', 'Download 4B…')}</button></div></div><div className="warm-settings-model"><header><strong>{t('Semantic Search')}</strong><span>{t(String(semantic.state ?? 'Unavailable'))}</span></header><p>{t('Embedding')}: {t(String(semantic.embedding ?? 'Unavailable'))} · {t('Reranker')}: {t(String(semantic.reranker ?? 'Unavailable'))}</p><p>{copy('语义模型缺失时不伪装为已安装。', 'Missing semantic models are never shown as installed.')}</p></div></div>}
          {section === 'providers' && <div className="warm-settings-section"><span className="warm-settings-eyebrow">CONNECTIONS</span><h3>{copy('AI 提供商', 'AI providers')}</h3><p>{copy('只启用你信任的服务。连接测试和四项验收仅报告真实响应。', 'Enable only services you trust. Connection and four-check acceptance tests report real responses only.')}</p><p className="warm-settings-note is-warning">{t('Cloud provider notice: when enabled, the relevant prompt and context may be sent to that provider. COMSOL models and files are not uploaded automatically.')}</p>{providers.map((provider) => { const config = configs[provider.name] ?? { enabled: provider.enabled, base_url: provider.base_url, model: provider.model, api_key: '' }; const result = tests[provider.name]; const accepted = acceptance[provider.name]; return <section className="warm-settings-provider" key={provider.name}><header><strong>{provider.label}</strong><span>{t(provider.status)}</span></header><label className="warm-settings-toggle"><input type="checkbox" checked={config.enabled} onChange={(event) => update(provider.name, 'enabled', event.target.checked)} />{copy('启用此提供商', 'Enable this provider')}</label><label>{t('Endpoint')}<input value={config.base_url} onChange={(event) => update(provider.name, 'base_url', event.target.value)} /></label><label>{t('Selected Model')}<input list={`warm-models-${provider.name}`} value={config.model} onChange={(event) => update(provider.name, 'model', event.target.value)} placeholder={t('Select or type a model')} /><datalist id={`warm-models-${provider.name}`}>{provider.available_models.map((model) => <option key={model} value={model} />)}</datalist></label>{provider.name !== 'ollama' && <label>{t('API Key (optional locally)')}<input type="password" value={config.api_key ?? ''} onChange={(event) => update(provider.name, 'api_key', event.target.value)} autoComplete="off" /></label>}<div className="warm-settings-metadata"><span>{t('Available Models')}: {provider.available_models.length ? provider.available_models.join(', ') : t('None reported')}</span><span>{t('Chat')}: {capability(provider.capabilities.chat, locale)}</span><span>{t('Tool Calling')}: {capability(provider.capabilities.tool_calling, locale)}</span><span>{t('Structured')}: {capability(provider.capabilities.structured_output, locale)}</span><span>{t('Context')}: {provider.capabilities.context_size?.toLocaleString() ?? t('Unknown')}</span></div><div className="warm-settings-provider-actions"><button type="button" onClick={() => void test(provider.name)} disabled={Boolean(working)}>{working === `test-${provider.name}` ? t('Testing…') : t('Test Connection')}</button><button type="button" onClick={() => void accept(provider.name)} disabled={Boolean(working)}>{working === `accept-${provider.name}` ? t('Running 4 tests…') : t('Acceptance Test')}</button><small>{result ? `${result.detail} · ${result.latency_ms} ms` : provider.detail}</small></div>{accepted && <div className={`warm-settings-acceptance ${accepted.success ? '' : 'is-failed'}`}><strong>{accepted.success ? '4/4 accepted' : accepted.detail}</strong>{accepted.tests.map((item) => <div key={item.name}><span>{item.success ? '✓' : '✕'} {item.label}</span><small>{item.detail} · {item.latency_ms} ms</small></div>)}</div>}</section> })}</div>}
          {section === 'retrieval' && <div className="warm-settings-section"><span className="warm-settings-eyebrow">KNOWLEDGE</span><h3>{copy('检索方式', 'Retrieval')}</h3><p>{copy('选择证据检索策略，不改变来源标题或科学验证门限。', 'Choose an evidence retrieval policy without changing source titles or scientific validation thresholds.')}</p><div className="warm-settings-row"><div><strong>{copy('检索模式', 'Retrieval mode')}</strong><span>{copy('运行前仍需检查证据是否充分。', 'Evidence sufficiency still needs checking before a claim.')}</span></div><select value={retrievalMode} onChange={(event) => setRetrievalMode(event.target.value as RetrievalMode)}><option value="fast">{t('Fast')} · BM25 + Dense + RRF</option><option value="accurate">{t('Accurate')} · Evidence default</option><option value="deep">{t('Deep Research')} · multi-query</option></select></div><p className="warm-settings-note">{copy('Knowledge Base 不随此设置被改写；检索结果仍需来源与引用。', 'This setting does not rewrite the Knowledge Base; retrieved evidence still needs sources and citations.')}</p></div>}
          {section === 'extensions' && <div className="warm-settings-section">
            <span className="warm-settings-eyebrow">OPTIONAL WORKFLOWS</span>
            <h3>{copy('按需开启扩展', 'Enable workflows only when needed')}</h3>
            <p>{copy('扩展默认关闭。关闭时后端也拒绝文献模型提取请求。', 'Extensions are off by default. The Runtime rejects literature extraction while disabled.')}</p>
            <div className="warm-settings-row">
              <div>
                <strong>{copy('文献模型提取与复刻计划', 'Literature-to-model proposal')}</strong>
                <span>{copy('导入 PDF、粘贴段落，或读取近三年的 arXiv/PMC 摘要；你决定是否进入建模计划。', 'Import a PDF, paste an excerpt, or read an arXiv/PMC abstract from the last three years; you decide whether to open a modeling plan.')}</span>
              </div>
              <label className="warm-settings-toggle"><input type="checkbox" checked={literatureEnabled} onChange={(event) => setLiteratureEnabled(event.target.checked)} />{copy('启用', 'Enable')}</label>
            </div>
            <div className="warm-settings-row">
              <div>
                <strong>{copy('DeepSeek 辅助提取', 'DeepSeek-assisted extraction')}</strong>
                <span>{copy('默认关闭。开启后，仅你主动提交的 PDF 可选文本片段或 arXiv/PMC 摘要发送给 DeepSeek；不上传 PDF 文件，不自动建模。密钥只存在运行进程内存或环境变量。', 'Off by default. Only bounded selectable PDF text or arXiv/PMC abstracts you submit are sent to DeepSeek; PDF bytes are never uploaded and COMSOL never runs automatically. The key stays in process memory or the environment.')}</span>
              </div>
              <label className="warm-settings-toggle"><input type="checkbox" checked={literatureDeepSeek} onChange={(event) => setLiteratureDeepSeek(event.target.checked)} />{copy('启用', 'Enable')}</label>
            </div>
            <p className="warm-settings-note">{deepseekAvailable === null ? copy('密钥状态尚未检测', 'Key status not checked') : deepseekAvailable ? copy('当前 Runtime 已检测到 DeepSeek 密钥', 'DeepSeek key detected in current Runtime') : copy('当前 Runtime 没有 DeepSeek 密钥', 'No DeepSeek key in current Runtime')}</p>
            <p className="warm-settings-note is-warning">{copy('请在「AI 提供商 → DeepSeek API」填写密钥并保存；密钥不会写入设置文件，重启后可能需要重填。云端请求失败时不会切换到其他模型或伪装成功。', 'Enter the key under AI providers → DeepSeek API and save. It is not written to settings and may need re-entry after restart. Cloud failure never silently falls back to another model.')}</p>
            <p className="warm-settings-note">{copy('本版只在你明确提交链接时读取 arXiv/PMC 摘要，不自动订阅或每月抓取。', 'This build reads arXiv/PMC abstracts only when you submit a link; it does not subscribe or fetch monthly.')}</p>
          </div>}
          {section === 'storage' && <div className="warm-settings-section"><span className="warm-settings-eyebrow">STORAGE</span><h3>{copy('存储与缓存', 'Storage & cache')}</h3><p>{copy('这里只能清理可重建缓存；不会触碰用户模型、文档、报告和模型权重。', 'Only rebuildable cache can be cleared here; user models, documents, reports and model weights are excluded.')}</p><div className="warm-settings-row"><div><strong>{t('Clear temporary cache')}</strong><span>{copy('临时文件可在需要时重新生成。', 'Temporary files can be recreated when needed.')}</span></div><button type="button" disabled={Boolean(working)} onClick={() => void clearStorage('temporary-cache')}>{copy('清理…', 'Clear…')}</button></div><div className="warm-settings-row"><div><strong>{t('Clear semantic index')}</strong><span>{copy('索引可重建；原始知识库保留。', 'The index can be rebuilt; the source Knowledge Base remains.')}</span></div><button type="button" disabled={Boolean(working)} onClick={() => void clearStorage('semantic-index')}>{copy('清理…', 'Clear…')}</button></div></div>}
          {notice && <p className="warm-settings-feedback" role="status">{notice}</p>}
        </main></div>
      <footer className="warm-settings-actions"><button type="button" onClick={onClose}>{t('Cancel')}</button><button type="button" className="is-primary" onClick={() => void save()} disabled={Boolean(working)}>{working === 'save' ? t('Detecting…') : copy('保存设置', 'Save settings')}</button></footer>
    </section>
  </div>
}

function normalizeRetrievalMode(value: ProviderSettings['retrieval_mode']): RetrievalMode { return value === 'basic' ? 'fast' : value === 'enhanced' ? 'accurate' : value }
function capability(value: boolean | null, locale: Locale): string { return value == null ? translate(locale, 'Unknown') : value ? translate(locale, 'Yes') : translate(locale, 'No') }
function formatBytes(value: number): string { if (!value) return '—'; const units = ['B', 'KiB', 'MiB', 'GiB', 'TiB']; let size = value; let index = 0; while (size >= 1024 && index < units.length - 1) { size /= 1024; index += 1 } return `${size.toFixed(1)} ${units[index]}` }
