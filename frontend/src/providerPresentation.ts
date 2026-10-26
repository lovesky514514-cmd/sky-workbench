import type { Provider } from './types'

export type ProviderGroup = 'local' | 'domestic' | 'international'

const domesticProviders = new Set(['kimi', 'deepseek', 'doubao'])

export function providerGroup(name: string, kind: string): ProviderGroup {
  if (kind === 'local') return 'local'
  return domesticProviders.has(name) ? 'domestic' : 'international'
}

export function initialProviderSelection(providers: Provider[], activeName: string) {
  const active = providers.find((provider) => provider.name === activeName)
  const preferred = active
    ?? providers.find((provider) => providerGroup(provider.name, provider.kind) === 'domestic')
    ?? providers.find((provider) => provider.kind === 'cloud')
    ?? providers.find((provider) => provider.kind === 'local')
  const group = preferred ? providerGroup(preferred.name, preferred.kind) : null

  // An unconfigured desktop should show the cloud connection form first.
  // An explicitly selected local provider keeps its own tab on reopen.
  return {
    name: preferred?.name ?? '',
    cloud: group !== 'local',
    international: group === 'international',
  }
}

// This describes only the current form, not a credential held by Runtime.
export function credentialLabel(enteredHere: boolean, cloud: boolean): 'current-session' | 'not-entered' | 'not-needed' {
  if (!cloud) return 'not-needed'
  return enteredHere ? 'current-session' : 'not-entered'
}
