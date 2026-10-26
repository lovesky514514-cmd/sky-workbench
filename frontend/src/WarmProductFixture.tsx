import { useState } from 'react'
import { WarmOnboarding } from './components/WarmOnboarding'
import { WarmSettings } from './components/WarmSettings'

// Test-only route: VITE_VISUAL_FIXTURES gates this module at the entry point.
// Its empty capability inputs are not COMSOL evidence or a scientific run.
export function WarmProductFixture({ page }: { page: 'onboarding' | 'settings' }) {
  const [locale, setLocale] = useState<'zh-CN' | 'en-US'>('zh-CN')
  return <><span className="warm-product-fixture-mark">DESIGN REVIEW · NOT SCIENTIFIC DATA</span>{page === 'onboarding'
    ? <WarmOnboarding locale={locale} environment={[]} providers={[]} activeProvider="" desktop={null} runtimeReady={false} runtimeUnavailable={false} onComplete={() => undefined} onRetryRuntime={() => undefined} onOpenLogs={() => undefined} onOpenHelp={() => undefined} />
    : <WarmSettings visualFixture locale={locale} providers={[]} settings={{ providers: {}, retrieval_mode: 'accurate', active_provider: '' }} onLocaleChange={setLocale} onOpenSetup={() => undefined} onClose={() => undefined} onSaved={async () => undefined} />}</>
}
