import { readFileSync } from 'node:fs'
import assert from 'node:assert/strict'

const read = (path) => readFileSync(new URL(path, import.meta.url), 'utf8')
const app = read('../src/App.tsx')
const warm = read('../src/WarmStudio.tsx')
const css = read('../src/styles.css') + read('../src/phase2.css')
const main = read('../src/main.tsx')
const tree = read('../src/components/ModelTree.tsx')
const canvas = read('../src/components/ScientificCanvas.tsx')
const inspector = read('../src/components/AgentControlPanel.tsx')
const dock = read('../src/components/TaskDock.tsx')
const timeline = read('../src/components/EvidenceTimeline.tsx')

assert.ok(app.includes('<WarmStudio'), 'App must mount the replacement warm workspace')
assert.ok(css.includes('--rail-collapsed: 64px'), 'collapsed rail token must be 64px')
assert.ok(css.includes('--execution-strip-collapsed: 50px'), 'collapsed execution strip token must be 50px')
assert.ok(css.includes('.instrument-workspace'), 'continuous work surface is missing')

assert.ok(tree.includes('instrument-rail'), 'model tree must be a collapsible instrument rail')
assert.ok(tree.includes('aria-expanded'), 'rail must expose its collapse state')
assert.ok(canvas.includes('scientific-plane'), 'canvas must render a continuous scientific plane')
assert.ok(warm.includes('warm-tabs'), 'scientific views must remain in compact top navigation')
assert.ok(!canvas.includes('readback-grid'), 'Phase 1 dashboard grid must be removed')

assert.ok(inspector.includes('context-inspector'), 'agent control must become a contextual inspector')
assert.ok(inspector.includes("'Action'"), 'inspector Action tab is required')
assert.ok(inspector.includes("'Properties'"), 'inspector Properties tab is required')
assert.ok(inspector.includes("'Evidence'"), 'inspector Evidence tab is required')
assert.ok(inspector.includes("'Diagnostics'"), 'inspector Diagnostics tab is required')
assert.ok(timeline.includes('execution-strip'), 'timeline must collapse into an execution strip')
assert.ok(timeline.includes('execution-drawer'), 'expanded execution drawer is required')
assert.ok(dock.includes('sessionState'), 'Task Dock must react to execution state')

assert.ok(main.includes('VITE_VISUAL_FIXTURES'), 'five-state screenshot fixture must be compile-time gated')
assert.ok(main.includes('Phase2VisualFixture'), 'Phase 2 visual fixture entry is missing')
assert.ok(!app.includes('className="panel phase1-agent-control"'), 'Phase 1 permanent right card must not remain')

console.log('Frontend Phase 2 Instrument Workspace contract: PASS')
