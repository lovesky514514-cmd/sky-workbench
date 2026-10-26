import assert from 'node:assert/strict'
import { createHash } from 'node:crypto'
import { readFileSync } from 'node:fs'

// These checks protect the frontend/backend boundary without executing COMSOL.
const read = (relative) => readFileSync(new URL(relative, import.meta.url), 'utf8')
const app = read('../src/App.tsx')
const studio = read('../src/WarmStudio.tsx')
const api = read('../src/api.ts')
const entry = read('../src/main.tsx')
const scientificView = read('../src/scientificView.ts')
const modelIdentity = read('../src/modelIdentity.ts')
const runUiPolicy = read('../src/runUiPolicy.ts')

assert.ok(app.includes('<WarmStudio'), 'production must mount the redesigned workspace')
assert.ok(studio.includes('warm-recovery-banner') && studio.includes('Recovered history is incomplete'), 'partially recovered local history must remain visibly unverified')
assert.ok(api.includes('/api/rc42/sessions/${sessionId}/domain'), 'model view must use frozen runtime projection')
assert.ok(studio.includes('currentScientificDomain(props.session, props.domain)'), 'production display must filter by session and attempt')
assert.ok(studio.includes('currentEvidenceState(props.session, domain)'), 'result display must use current evidence state')
assert.ok(studio.includes('verifiedCurrentResult(props.session, domain)'), 'result display must use the authoritative current verdict')
assert.ok(scientificView.includes('current.authoritativeVerdict'), 'the verifier must inspect the authoritative verdict')
assert.ok(studio.includes('completed ? <VerifiedArtifactViewer'), 'only qualified results can load checked artifacts')
assert.ok(studio.includes('canRunReviewedPlan(props.plan, props.session?.id, reviewedPlanId'), 'RUN must use current-session review policy')
assert.ok(runUiPolicy.includes('reviewedPlanId === `${sessionId}:${plan.plan_id}:${plan.updated_at}`'), 'review must bind to session and revision')
assert.ok(app.includes('reviewedBoundary(selectedDomain, review)'), 'approval must bind to exact current scientific object')
assert.ok(modelIdentity.includes('review.attemptId !== domain.runState.attemptId'), 'boundary review must bind to the current attempt')
assert.ok(modelIdentity.includes('item?.id === review.objectId'), 'boundary review must match trusted sourceId')
assert.ok(app.indexOf('if (selectedDomain?.reviewRequest && session)') < app.indexOf("if (pendingIntent) { setPendingIntent(null)"), 'live review rejection must take priority over a draft')
assert.ok(entry.includes("VITE_VISUAL_FIXTURES === '1'"), 'illustrative fixture must remain gated')

// The approved brand icon is reused byte-for-byte from the existing asset.
const icon = readFileSync(new URL('../src/assets/brand/workbench-icon-64.png', import.meta.url))
assert.equal(createHash('sha256').update(icon).digest('hex'), '15bbfcc287fbb5ec93f3cb61e7829780ca5789366e47bd1f18fb7038e06bc125')
console.log('Warm Scientific Studio frontend contract: PASS')
