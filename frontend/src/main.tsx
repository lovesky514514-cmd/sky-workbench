import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import App from './App'
import type { Phase2VisualState } from './components/ScientificCanvas'
import './styles.css'
import './phase2.css'
import './phase21.css'
import './warm-studio.css'
import './sky-pet.css'
import './warm-plan.css'
import './warm-fixture.css'
import './warm-product-surfaces.css'

async function mount() {
  let screen = <App />
  // Visual review fixtures are imported only when their explicit dev flag is
  // enabled. Production bundles must not carry synthetic model/verdict data.
  if (import.meta.env.VITE_VISUAL_FIXTURES === '1') {
    const query = new URLSearchParams(window.location.search)
    const product = query.get('warm-product-fixture')
    const warm = query.get('warm-fixture')
    const phase2 = query.get('phase2-fixture') as Phase2VisualState | null
    const phase2States: Phase2VisualState[] = ['idle', 'plan-review', 'running', 'need-review', 'verified-result', 'left-rail-expanded', 'inspector-object-edit']
    if (product === 'onboarding' || product === 'settings') {
      const { WarmProductFixture } = await import('./WarmProductFixture')
      screen = <WarmProductFixture page={product} />
    } else if (warm === 'task' || warm === 'plan' || warm === 'model' || warm === 'results') {
      const { WarmStudioFixture } = await import('./WarmStudioFixture')
      screen = <WarmStudioFixture page={warm} />
    } else if (phase2 && phase2States.includes(phase2)) {
      const { Phase2VisualFixture } = await import('./Phase2VisualFixture')
      screen = <Phase2VisualFixture visualState={phase2} />
    }
  }
  createRoot(document.getElementById('root')!).render(<StrictMode>{screen}</StrictMode>)
}

void mount()
