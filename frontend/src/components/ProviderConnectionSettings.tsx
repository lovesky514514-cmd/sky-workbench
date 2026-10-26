import { useEffect, useState } from 'react'
import { translate, type Locale } from '../i18n'
import { credentialLabel, initialProviderSelection, providerGroup } from '../providerPresentation'
import type { Provider, ProviderAcceptanceResult, ProviderConfig, ProviderTestResult } from '../types'

type Props = {
  locale: Locale
  providers: Provider[]
  configs: Record<string, ProviderConfig>
  activeProvider: string
  savedProvider: string
  working: string
  tests: Record<string, ProviderTestResult>
  acceptance: Record<string, ProviderAcceptanceResult>
  onUpdate: (name: string, field: keyof ProviderConfig, value: string | boolean) => void
  onTest: (name: string) => void
  onAccept: (name: string) => void
  onSelect: (name: string) => void
}

export function ProviderConnectionSettings(props: Props) {
  const { locale, providers, configs, activeProvider, savedProvider, working, tests, acceptance } = props
  const copy = (cn: string, en: string) => locale === 'zh-CN' ? cn : en
  const t = (value: string) => translate(locale, value)
  const initial = initialProviderSelection(providers, activeProvider)
  const [showCloudProviders, setShowCloudProviders] = useState(initial.cloud)
  const [showInternational, setShowInternational] = useState(initial.international)
  const [selectedProviderName, setSelectedProviderName] = useState(initial.name)

  const localProviders = providers.filter((provider) => providerGroup(provider.name, provider.kind) === 'local')
  const domesticProviders = providers.filter((provider) => providerGroup(provider.name, provider.kind) === 'domestic')
  const internationalProviders = providers.filter((provider) => providerGroup(provider.name, provider.kind) === 'international')

  // Provider discovery may arrive after Settings opens. Select a real entry
  // only when the current selection has not been supplied by the Runtime.
  useEffect(() => {
    if (selectedProviderName || !initial.name) return
    setSelectedProviderName(initial.name)
    setShowCloudProviders(initial.cloud)
    setShowInternational(initial.international)
  }, [initial.name, initial.cloud, initial.international, selectedProviderName])

  const selectedProvider = providers.find((provider) => provider.name === selectedProviderName)
  const selectedGroup = selectedProvider ? providerGroup(selectedProvider.name, selectedProvider.kind) : null
  const visibleSelection = selectedProvider && (showCloudProviders
    ? selectedGroup === 'domestic' || selectedGroup === 'international'
    : selectedGroup === 'local')
  const selectedConfig = selectedProvider
    ? configs[selectedProvider.name] ?? {
      enabled: selectedProvider.enabled,
      base_url: selectedProvider.base_url,
      model: selectedProvider.model,
      api_key: '',
    }
    : null

  const selectLocal = () => {
    setShowCloudProviders(false)
    if (selectedGroup !== 'local') setSelectedProviderName(localProviders[0]?.name ?? '')
  }
  const selectCloud = () => {
    setShowCloudProviders(true)
    if (selectedGroup === 'local' || !selectedGroup) {
      setSelectedProviderName(domesticProviders[0]?.name ?? internationalProviders[0]?.name ?? '')
    }
  }

  return <div className="warm-settings-section warm-provider-setup">
    <h3>{copy('连接 AI', 'Connect AI')}</h3>
    <div className="warm-provider-mode" role="group" aria-label={copy('连接类型', 'Connection type')}>
      <button type="button" className={!showCloudProviders ? 'is-selected' : ''} onClick={selectLocal}>
        {copy('本机', 'On this computer')}
      </button>
      <button type="button" className={showCloudProviders ? 'is-selected' : ''} onClick={selectCloud}>
        {copy('云端 API', 'Cloud API')}
      </button>
    </div>

    {!showCloudProviders && <div className="warm-provider-group">
      <div className="warm-provider-choices">
        {localProviders.filter((provider) => provider.name !== 'openai_compatible').map((provider) =>
          <ProviderChoice key={provider.name} provider={provider} selected={selectedProviderName === provider.name}
            onChoose={setSelectedProviderName} />)}
      </div>
      {localProviders.some((provider) => provider.name === 'openai_compatible') &&
        <details className="warm-provider-local-more" open={selectedProviderName === 'openai_compatible' ? true : undefined}>
          <summary>{copy('兼容本地端点', 'Compatible local endpoint')}</summary>
          <div className="warm-provider-choices">
            {localProviders.filter((provider) => provider.name === 'openai_compatible').map((provider) =>
              <ProviderChoice key={provider.name} provider={provider} selected={selectedProviderName === provider.name}
                onChoose={setSelectedProviderName} />)}
          </div>
        </details>}
    </div>}

    {showCloudProviders && <div className="warm-provider-cloud">
      <div className="warm-provider-choices">
        {domesticProviders.map((provider) => <ProviderChoice key={provider.name} provider={provider}
          selected={selectedProviderName === provider.name} onChoose={setSelectedProviderName} />)}
      </div>
      <button type="button" className="warm-provider-more" aria-expanded={showInternational}
        onClick={() => setShowInternational((value) => !value)}>
        {showInternational ? copy('收起其他服务', 'Hide other services') : copy('其他服务', 'Other services')}
      </button>
      {showInternational && <div className="warm-provider-choices">
        {internationalProviders.map((provider) => <ProviderChoice key={provider.name} provider={provider}
          selected={selectedProviderName === provider.name} onChoose={setSelectedProviderName} />)}
      </div>}
    </div>}

    {visibleSelection && selectedProvider && selectedConfig &&
      <section className="warm-settings-provider" aria-label={selectedProvider.label}>
        <header><strong>{selectedProvider.label}</strong><span>{t(selectedProvider.status)}</span></header>
        <label className="warm-settings-toggle">
          <input type="checkbox" checked={selectedConfig.enabled} disabled={Boolean(working)}
            onChange={(event) => props.onUpdate(selectedProvider.name, 'enabled', event.target.checked)} />
          {copy('启用', 'Enable')}
        </label>
        <label>{copy('模型', 'Model')}
          <input list={`warm-models-${selectedProvider.name}`} value={selectedConfig.model}
            disabled={Boolean(working)} onChange={(event) => props.onUpdate(selectedProvider.name, 'model', event.target.value)}
            placeholder={copy('输入服务商的模型 ID', 'Enter the provider model ID')} />
          <datalist id={`warm-models-${selectedProvider.name}`}>
            {selectedProvider.available_models.map((model) => <option key={model} value={model} />)}
          </datalist>
        </label>
        {selectedProvider.kind === 'cloud' && <label>{copy('API Key', 'API key')}
          <input type="password" value={selectedConfig.api_key ?? ''} disabled={Boolean(working)}
            onChange={(event) => props.onUpdate(selectedProvider.name, 'api_key', event.target.value)}
            autoComplete="new-password" spellCheck={false} placeholder={copy('输入后不会回显', 'Never echoed after entry')} />
          <small>{credentialLabel(Boolean(selectedConfig.api_key), true) === 'current-session'
            ? copy('仅保留在当前 Runtime 会话', 'Held only in the current Runtime session')
            : copy('本窗口未输入；也可能由运行环境提供', 'Not entered here; the Runtime may supply it')}</small>
        </label>}
        <div className="warm-settings-provider-actions">
          <button type="button" onClick={() => props.onTest(selectedProvider.name)}
            disabled={Boolean(working) || !selectedConfig.enabled || !selectedConfig.model}>
            {working === `test-${selectedProvider.name}` ? t('Testing…') : t('Test Connection')}
          </button>
          <button type="button" className="is-primary" onClick={() => props.onSelect(selectedProvider.name)}
            disabled={!tests[selectedProvider.name]?.success || Boolean(working)}>
            {activeProvider === selectedProvider.name
              ? activeProvider === savedProvider
                ? copy('当前配置', 'Selected provider')
                : copy('已选择 · 请保存设置', 'Selected · save settings below')
              : copy('设为当前提供商', 'Use this provider')}
          </button>
        </div>
        {tests[selectedProvider.name] && <p className={`warm-provider-result ${tests[selectedProvider.name].success ? 'is-ready' : 'is-error'}`} role="status">
          {tests[selectedProvider.name].success
            ? copy('连接测试通过', 'Connection test passed')
            : copy('连接失败，请检查密钥、模型和端点', 'Connection failed; check the key, model and endpoint')}
          {' · '}{tests[selectedProvider.name].latency_ms} ms
        </p>}
        <details className="warm-provider-advanced">
          <summary>{copy('端点与高级诊断', 'Endpoint and diagnostics')}</summary>
          {selectedProvider.kind === 'cloud' && <p className="warm-provider-privacy">
            {copy('云端请求会发送你提交的相关文本；模型文件不会自动上传。',
              'Cloud requests send relevant submitted text; model files are not uploaded automatically.')}
          </p>}
          <label>{t('Endpoint')}
            <input value={selectedConfig.base_url} disabled={Boolean(working)}
              onChange={(event) => props.onUpdate(selectedProvider.name, 'base_url', event.target.value)} />
          </label>
          <div className="warm-settings-metadata">
            <span>{t('Available Models')}: {selectedProvider.available_models.length
              ? selectedProvider.available_models.join(', ') : t('None reported')}</span>
            <span>{t('Chat')}: {capability(selectedProvider.capabilities.chat, locale)}</span>
            <span>{t('Structured')}: {capability(selectedProvider.capabilities.structured_output, locale)}</span>
            <span>{t('Context')}: {selectedProvider.capabilities.context_size?.toLocaleString() ?? t('Unknown')}</span>
          </div>
          <button type="button" onClick={() => props.onAccept(selectedProvider.name)} disabled={Boolean(working)}>
            {working === `accept-${selectedProvider.name}` ? t('Running 4 tests…') : t('Acceptance Test')}
          </button>
          {acceptance[selectedProvider.name] &&
            <div className={`warm-settings-acceptance ${acceptance[selectedProvider.name].success ? '' : 'is-failed'}`}>
              <strong>{acceptance[selectedProvider.name].success ? '4/4 accepted' : acceptance[selectedProvider.name].detail}</strong>
              {acceptance[selectedProvider.name].tests.map((item) => <div key={item.name}>
                <span>{item.success ? '✓' : '✕'} {item.label}</span>
                <small>{item.detail} · {item.latency_ms} ms</small>
              </div>)}
            </div>}
        </details>
      </section>}
    <button type="button" className="warm-provider-auto" onClick={() => props.onSelect('')}>
      {copy('改用本地自动选择', 'Use automatic local selection')}
    </button>
    <p className="warm-provider-current" role="status">
      {activeProvider !== savedProvider
        ? copy('待保存', 'Pending save') : copy('当前配置', 'Configured provider')}: {' '}
      {providers.find((provider) => provider.name === activeProvider)?.label
        ?? copy('本地自动', 'Automatic local')}
    </p>
  </div>
}

function capability(value: boolean | null, locale: Locale): string {
  return value == null ? translate(locale, 'Unknown') : translate(locale, value ? 'Yes' : 'No')
}

function ProviderChoice({ provider, selected, onChoose }: {
  provider: Provider
  selected: boolean
  onChoose: (name: string) => void
}) {
  return <button type="button" className={selected ? 'is-selected' : ''}
    aria-pressed={selected} onClick={() => onChoose(provider.name)}>{provider.label}</button>
}
