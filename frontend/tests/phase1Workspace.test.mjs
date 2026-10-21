import { readFileSync } from 'node:fs'
import assert from 'node:assert/strict'

const app = readFileSync(new URL('../src/App.tsx', import.meta.url), 'utf8')
const warm = readFileSync(new URL('../src/WarmStudio.tsx', import.meta.url), 'utf8')
const css = readFileSync(new URL('../src/styles.css', import.meta.url), 'utf8')
const components = new Map([
  ['ScientificCanvas', readFileSync(new URL('../src/components/ScientificCanvas.tsx', import.meta.url), 'utf8')],
  ['ModelTree', readFileSync(new URL('../src/components/ModelTree.tsx', import.meta.url), 'utf8')],
  ['AgentControlPanel', readFileSync(new URL('../src/components/AgentControlPanel.tsx', import.meta.url), 'utf8')],
  ['EvidenceTimeline', readFileSync(new URL('../src/components/EvidenceTimeline.tsx', import.meta.url), 'utf8')],
  ['HumanInterventionPanel', readFileSync(new URL('../src/components/HumanInterventionPanel.tsx', import.meta.url), 'utf8')],
])

for (const [name, source] of components) {
  assert.ok(source.includes('export function ' + name), 'missing component export ' + name)
  // Phase 1 components remain available for the legacy visual fixture, while
  // production intentionally mounts the approved warm workspace instead.
  assert.ok(app.includes('<WarmStudio'), 'production workspace is not mounted')
}

assert.ok(warm.includes("['ASK', 'PLAN', 'RUN']"), 'ASK/PLAN/RUN must remain available')
assert.ok(!app.includes('className="panel conversation-panel"'), 'chat must not remain the central panel')
assert.ok(css.includes('grid-template-areas:'))
assert.ok(css.includes('"tree canvas control"'))
assert.ok(css.includes('"tree timeline timeline"'))
assert.ok(warm.includes('warm-task-focus'), 'the scientific question must remain primary')

const canvas = components.get('ScientificCanvas')
assert.ok(canvas.includes('Verified readback'))
assert.ok(canvas.includes('Unavailable'))
assert.ok(canvas.includes('No 3D representation is generated'))
assert.ok(!canvas.includes('three.js'))
assert.ok(!canvas.includes('WebGL'))

const intervention = components.get('HumanInterventionPanel')
for (const action of ['Pause', 'Modify', 'Approve', 'Reject', 'Resume']) {
  assert.ok(intervention.includes(action), 'missing human action ' + action)
}
assert.ok(intervention.includes('does not certify scientific success'))

console.log('Frontend Phase 1 Scientific Agent Workspace contract: PASS')
