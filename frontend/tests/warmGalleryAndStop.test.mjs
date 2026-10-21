import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

const read = (relative) => readFileSync(new URL(relative, import.meta.url), 'utf8')
const viewer = read('../src/components/VerifiedArtifactViewer.tsx')
const selection = read('../src/components/verifiedImageArtifacts.ts')
const studio = read('../src/WarmStudio.tsx')
const app = read('../src/App.tsx')
const plan = read('../src/components/WarmPlanOverview.tsx')

// Gallery slides must come from the current, completed run's evidence manifest.
assert.ok(viewer.includes('currentRunId') && selection.includes('message.run_id !== currentRunId'))
assert.ok(selection.includes('Object.entries(message.artifacts') && selection.includes('evidence.artifacts?.[key]'))
assert.ok(viewer.includes('gallery-next') && viewer.includes('gallery-previous'))
assert.ok(viewer.includes('artifact-zoom-dialog') && viewer.includes('aria-label={t(\'Close enlarged image\')}'))
assert.ok(!viewer.includes('<code>{detail}</code>'), 'raw API exceptions must not be rendered')

// The primary plan view is a readable, semantic review; technical PlanCard stays optional.
assert.ok(studio.includes('<WarmPlanOverview') && studio.includes('warm-full-plan'))
assert.ok(!studio.includes('className="warm-full-plan" open'))
assert.ok(plan.includes('missing_information') && plan.includes('assumptions') && plan.includes('boundary_conditions'))

// Emergency controls are visible outside the model tab, but Resume is only PAUSED.
assert.ok(studio.includes('warm-emergency-controls'))
assert.ok(studio.includes("props.session?.state === 'PAUSED'"))
assert.ok(app.includes('emergencyStop'))
assert.ok(app.includes('api.cancelRun(session.id, true)'), 'unsafe-to-pause execution requires explicit owned-process stop')
console.log('Warm gallery, plan and stop contract: PASS')
