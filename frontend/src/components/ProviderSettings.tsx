import { useEffect, useState } from 'react'
import { api } from '../api'
import type { Provider, ProviderAcceptanceResult, ProviderConfig, ProviderSettings, ProviderTestResult, RetrievalMode } from '../types'
import { currentLocale, translate } from '../i18n'

export function ProviderSettingsDialog({ providers, settings, onClose, onSaved }: { providers: Provider[]; settings: ProviderSettings; onClose: () => void; onSaved: () => Promise<void> }) {
  const t = (source: string) => translate(currentLocale(), source)
  const [configs, setConfigs] = useState<Record<string, ProviderConfig>>(() => Object.fromEntries(Object.entries(settings.providers).map(([name, config]) => [name, { ...config, api_key: '' }])))
  const [activeProvider, setActiveProvider] = useState(settings.active_provider || '')
  const [retrievalMode, setRetrievalMode] = useState<RetrievalMode>(() => normalizeRetrievalMode(settings.retrieval_mode))
  const [tests, setTests] = useState<Record<string, ProviderTestResult>>({})
  const [acceptance, setAcceptance] = useState<Record<string, ProviderAcceptanceResult>>({})
  const [working, setWorking] = useState('')
  const [localModels, setLocalModels] = useState<Record<string, unknown>>({})
  const [modelNotice, setModelNotice] = useState('')
  const [storageNotice, setStorageNotice] = useState('')
  const refreshModels = async () => setLocalModels(await api.modelsStatus())
  useEffect(() => { void refreshModels().catch((cause) => setModelNotice(String(cause))) }, [])
  const update = (name: string, field: keyof ProviderConfig, value: string | boolean) => setConfigs((current) => ({ ...current, [name]: { ...current[name], [field]: value } }))
  const save = async () => { setWorking('save'); try { await api.saveProviders(configs, retrievalMode, activeProvider); await onSaved() } finally { setWorking('') } }
  const test = async (name: string) => {
    setWorking(name)
    try { await api.saveProviders(configs, retrievalMode, activeProvider); const result = await api.testProvider(name); setTests((current) => ({ ...current, [name]: result })) } finally { setWorking('') }
  }
  const accept = async (name: string) => {
    setWorking(`accept-${name}`)
    try { await api.saveProviders(configs, retrievalMode, activeProvider); const result = await api.acceptProvider(name); setAcceptance((current) => ({ ...current, [name]: result })) } finally { setWorking('') }
  }
  const clearStorage = async (kind: 'temporary-cache' | 'semantic-index') => {
    if (!window.confirm(t('Only rebuildable cache will be removed. Scientific models, documents and reports are preserved.'))) return
    const result = await api.clearStorage(kind)
    setStorageNotice(`${t('Storage cleanup complete')}: ${JSON.stringify(result)}`)
    await refreshModels()
  }
  return <div className="dialog-backdrop" onMouseDown={onClose}><div className="settings-dialog provider-settings-v2" role="dialog" aria-modal="true" aria-label={t('AI Providers')} onMouseDown={(event) => event.stopPropagation()}>
    <div className="dialog-header"><div><span className="eyebrow">{t('WORKBENCH SETTINGS')}</span><h2>{t('AI Providers')}</h2></div><button onClick={onClose} aria-label={t('Close')}>×</button></div>
    <div className="provider-settings-toolbar"><label>{t('Active Provider')}<select value={activeProvider} onChange={(event) => setActiveProvider(event.target.value)}><option value="">{t('Automatic local-first')}</option>{providers.map((provider) => <option value={provider.name} key={provider.name}>{provider.label}</option>)}</select></label><label>{t('Retrieval')}<select value={retrievalMode} onChange={(event) => setRetrievalMode(event.target.value as RetrievalMode)}><option value="fast">{t('Fast')} · BM25 + Dense + RRF</option><option value="accurate">{t('Accurate')} · Evidence default</option><option value="deep">{t('Deep Research')} · multi-query</option></select></label></div>
    <p className="settings-intro">{t('Detection never blocks startup. Keys stay in backend memory only. Tool calling and structured-output support remain Unknown unless the server explicitly advertises them.')}</p>
    <p className="cloud-provider-warning">{t('Cloud provider notice: when enabled, the relevant prompt and context may be sent to that provider. COMSOL models and files are not uploaded automatically.')}</p>
    <ModelCenter models={localModels} notice={modelNotice} onNotice={setModelNotice} onRefresh={refreshModels} />
    <div className="settings-providers">{providers.map((provider) => {
      const config = configs[provider.name] ?? { enabled: provider.enabled, base_url: provider.base_url, model: provider.model, api_key: '' }
      const result = tests[provider.name]
      const acceptanceResult = acceptance[provider.name]
      return <fieldset key={provider.name} className="provider-card"><legend><label><input type="checkbox" checked={config.enabled} onChange={(event) => update(provider.name, 'enabled', event.target.checked)} />{provider.label}</label><span className={`provider-state ${provider.status.toLowerCase().replaceAll(' ', '-')}`}>{provider.status}</span></legend>
        <label className="provider-endpoint">{t('Endpoint')}<input value={config.base_url} onChange={(event) => update(provider.name, 'base_url', event.target.value)} /></label>
        <label>{t('Selected Model')}<input list={`models-${provider.name}`} value={config.model} onChange={(event) => update(provider.name, 'model', event.target.value)} placeholder={t('Select or type a model')} /><datalist id={`models-${provider.name}`}>{provider.available_models.map((model) => <option key={model} value={model} />)}</datalist></label>
        {provider.name !== 'ollama' && <label>{t('API Key (optional locally)')}<input type="password" value={config.api_key ?? ''} onChange={(event) => update(provider.name, 'api_key', event.target.value)} autoComplete="off" /></label>}
        <div className="provider-models"><strong>{t('Available Models')}</strong>{provider.model_details.length ? provider.model_details.map((model) => <span key={model.name}><b>{model.name}</b> · {model.size_label}{model.parameter_size ? ` · ${model.parameter_size}` : ''}{model.quantization_level ? ` · ${model.quantization_level}` : ''}</span>) : <span>{provider.available_models.length ? provider.available_models.join(', ') : t('None reported')}</span>}</div>
        <div className="capability-grid"><Capability label={t('Chat')} value={provider.capabilities.chat} /><Capability label={t('Streaming')} value={provider.capabilities.streaming} /><Capability label={t('Tool Calling')} value={provider.capabilities.tool_calling} /><Capability label={t('Structured')} value={provider.capabilities.structured_output} /><div><small>{t('Context')}</small><strong>{provider.capabilities.context_size?.toLocaleString() ?? t('Unknown')}</strong></div><div><small>{t('Location')}</small><strong>{t(provider.capabilities.location)}</strong></div></div>
        <div className="provider-test-row"><button onClick={() => void test(provider.name)} disabled={Boolean(working)}>{working === provider.name ? t('Testing…') : t('Test Connection')}</button><button onClick={() => void accept(provider.name)} disabled={Boolean(working)}>{working === `accept-${provider.name}` ? t('Running 4 tests…') : t('Acceptance Test')}</button><span className={result?.success ? 'test-success' : 'test-failure'}>{result ? `${result.detail} · ${result.latency_ms} ms` : provider.detail}</span></div>
        {acceptanceResult && <div className={`acceptance-results ${acceptanceResult.success ? 'passed' : 'failed'}`}><strong>{acceptanceResult.success ? '4/4 accepted' : acceptanceResult.detail}</strong>{acceptanceResult.tests.length ? acceptanceResult.tests.map((item) => <div key={item.name}><span>{item.success ? '✓' : '✕'} {item.label}</span><small>{item.detail} · {item.latency_ms} ms</small></div>) : <small>{acceptanceResult.detail}</small>}</div>}
      </fieldset>
    })}</div>
    <div className="local-ai-guide"><strong>{t('Set up free local AI — recommended first')}</strong><div><b>1 · Ollama</b><span>{t('Choose a recommended model explicitly; no model is downloaded automatically.')}</span></div><div><b>2 · LM Studio</b><span>{t('Download a model, start the Local Server, and keep the OpenAI-compatible endpoint at port 1234.')}</span></div><div><b>3 · Cloud API</b><span>{t('Optional. Enable a cloud provider only when local inference is not suitable.')}</span></div><small>{t('The Workbench never installs system software or downloads a model without your confirmation.')}</small></div>
    <section className="storage-controls"><strong>{t('Storage')}</strong><span>{t('Clear only rebuildable application cache. User MPH, documents, reports and model weights are never included.')}</span><div><button onClick={() => void clearStorage('temporary-cache')}>{t('Clear temporary cache')}</button><button onClick={() => void clearStorage('semantic-index')}>{t('Clear semantic index')}</button></div>{storageNotice && <small>{storageNotice}</small>}</section>
    <div className="dialog-actions"><button className="button secondary" onClick={onClose}>{t('Cancel')}</button><button className="button primary" onClick={() => void save()} disabled={working === 'save'}>{working === 'save' ? t('Detecting…') : t('Save, detect & select')}</button></div>
  </div></div>
}

function Capability({ label, value }: { label: string; value: boolean | null }) { return <div><small>{label}</small><strong>{value == null ? 'Unknown' : value ? 'Yes' : 'No'}</strong></div> }
function ModelCenter({ models, notice, onNotice, onRefresh }: { models: Record<string, unknown>; notice: string; onNotice: (value: string) => void; onRefresh: () => Promise<void> }) {
  const locale = currentLocale(); const t = (source: string) => translate(locale, source)
  const ollama = (models.ollama ?? {}) as Record<string, unknown>; const semantic = (models.semantic ?? {}) as Record<string, unknown>
  const hardware = (ollama.hardware ?? {}) as Record<string, unknown>; const tier = String(ollama.recommendation ?? 'Balanced')
  const recommended = tier === 'Light' ? 'qwen3:4b' : tier === 'Strong' ? 'gpt-oss:20b' : 'qwen3:8b'
  const pull = async () => {
    const preview = await api.pullOllama(recommended, false)
    const message = `${recommended}\n${String(preview.estimated_download ?? '')}\n${String(preview.storage ?? '')}`
    if (!window.confirm(`${t('Large model download confirmation')}\n\n${message}`)) return
    await api.pullOllama(recommended, true); onNotice(t('Download started')); await onRefresh()
  }
  return <section className="model-center"><header><div><span className="eyebrow">{t('MODEL CENTER')}</span><strong>{t('Local LLM')}</strong></div><button onClick={() => void onRefresh()}>{t('Detect')}</button></header><div className="model-center-grid"><div><small>Ollama</small><strong>{t(String(ollama.state ?? 'Detecting…'))}</strong><span>{String(ollama.detail ?? '')}</span><button disabled={ollama.state === 'Not Installed'} onClick={() => void api.startOllama().then(onRefresh)}>{t('Start Ollama')}</button></div><div><small>{t('Recommended model')}</small><strong>{recommended}</strong><span>{t(tier)} · RAM {formatBytes(Number(hardware.ram_bytes ?? 0))} · VRAM {formatBytes(Number(((hardware.gpu ?? {}) as Record<string, unknown>).vram_bytes ?? 0))}</span><button disabled={ollama.state === 'Not Installed'} onClick={() => void pull()}>{t('Download')}</button></div><div><small>{t('Semantic Search')}</small><strong>{t(String(semantic.state ?? 'Not Installed'))}</strong><span>{t('Embedding')}: {t(String(semantic.embedding ?? 'Not Installed'))} · {t('Reranker')}: {t(String(semantic.reranker ?? 'Not Installed'))}</span><button onClick={() => onNotice(`${String(semantic.models ?? '')} · ${String(semantic.estimated_download ?? '')} · ${String(semantic.install_path ?? '')}`)}>{t('Review Semantic Pack installation')}</button></div></div>{notice && <p className="model-center-notice">{notice}</p>}</section>
}
function formatBytes(value: number): string { if (!value) return '—'; const units = ['B', 'KiB', 'MiB', 'GiB', 'TiB']; let size = value; let index = 0; while (size >= 1024 && index < units.length - 1) { size /= 1024; index += 1 } return `${size.toFixed(1)} ${units[index]}` }
function normalizeRetrievalMode(value: ProviderSettings['retrieval_mode']): RetrievalMode { return value === 'basic' ? 'fast' : value === 'enhanced' ? 'accurate' : value }
