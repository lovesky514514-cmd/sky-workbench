// Display only a version reported by installation discovery or a live Bridge.
// A discovered executable cannot certify a Bridge attachment or a solve.
function versionLabel(raw: string): string {
  const match = /^(?:COMSOL\s+)?(\d+\.\d+)$/i.exec(raw.trim())
  return match ? `COMSOL ${match[1]}` : 'COMSOL'
}

export function comsolHomeStatus(
  detectedVersion: string,
  installed: boolean,
  bridgeVersion: string,
  bridgeConnected: boolean,
  zh: boolean,
): string {
  if (bridgeConnected) return `${versionLabel(bridgeVersion || detectedVersion)} ${zh ? '已连接' : 'connected'}`
  if (installed) return `${versionLabel(detectedVersion)} ${zh ? '已检测 · Bridge 未验证' : 'detected · Bridge unverified'}`
  return zh ? 'COMSOL 不可用' : 'COMSOL unavailable'
}

export function environmentReadiness(
  runtimeReady: boolean,
  comsolDetected: boolean,
  providerReady: boolean,
): 'READY' | 'DEGRADED' | 'BLOCKED' {
  if (!runtimeReady) return 'BLOCKED'
  return comsolDetected && providerReady ? 'READY' : 'DEGRADED'
}

type ProviderReadiness = {
  name: string
  kind: 'local' | 'cloud'
  status: string
  model: string
  available_models: string[]
}

export function verifiedProviderReady(providers: ProviderReadiness[], activeProvider: string): boolean {
  const selected = activeProvider
    ? providers.filter((provider) => provider.name === activeProvider)
    : providers.filter((provider) => provider.kind === 'local')
  return selected.some((provider) => provider.status === 'Ready'
    && provider.model.length > 0
    && provider.available_models.includes(provider.model))
}

export function detectedScientificEngines(scan: Record<string, unknown>): string[] {
  const supported = new Set(['MATLAB', 'Wolfram', 'Materials Studio'])
  if (!Array.isArray(scan.adapters)) return []
  return scan.adapters.flatMap((value: unknown) => {
    if (!value || typeof value !== 'object') return []
    const adapter = value as Record<string, unknown>
    return adapter.installed === true && typeof adapter.engine === 'string' && supported.has(adapter.engine)
      ? [adapter.engine] : []
  })
}

export function scientificAdapterSummary(installed: boolean, isComsol: boolean, zh: boolean): string {
  if (!installed) return zh ? '未检测到受支持的本机入口' : 'No supported local installation found'
  if (isComsol) return zh
    ? '已检测到安装；Bridge、模型读回和求解仍需验证'
    : 'Installation detected; Bridge, model readback and solve remain unverified'
  return zh
    ? '已检测到安装；许可证、模型读回和执行仍需验证'
    : 'Installation detected; license, model readback and execution remain unverified'
}
