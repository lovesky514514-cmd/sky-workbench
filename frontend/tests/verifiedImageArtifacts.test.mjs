import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import ts from 'typescript'

// Compile only the pure selector; type-only imports vanish and no API or COMSOL
// process is needed. These are contract tests, not scientific image evidence.
const source = readFileSync(new URL('../src/components/verifiedImageArtifacts.ts', import.meta.url), 'utf8')
const compiled = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS } }).outputText
const exports = {}
new Function('exports', compiled)(exports)
const select = exports.verifiedImageArtifacts

const verdict = { completed: true, command_success: true, model_state_verified: true, scientific_validation_success: true, evidence_contract_passed: true, run_id: 'run-1' }
const evidence = { passed: true, run_id: 'run-1', attempt_id: 'attempt-1', artifacts: {
  temperature_image: { path: 'C:/private/temperature.png', sha256: 'hash-temperature' },
  python_plot: { path: 'C:/private/plot.png', sha256: 'hash-plot' },
  model: { path: 'C:/private/model.mph', sha256: 'hash-model' },
} }
const message = { run_id: 'run-1', execution_verdict: verdict, evidence_contract: evidence, artifacts: {
  temperature_image: 'C:/private/temperature.png', python_plot: 'C:/private/plot.png', model: 'C:/private/model.mph',
} }
const session = { messages: [message] }

const slides = select(session, 'run-1', 'attempt-1')
assert.equal(slides.length, 2)
assert.deepEqual(slides.map((item) => item.filename), ['temperature.png', 'plot.png'])
assert.equal(slides[0].name, 'Temperature field')
assert.equal(slides[1].name, 'plot.png')
assert.equal(slides[1].quantity, '')
assert.equal(select(session, 'other-run', 'attempt-1').length, 0)
assert.equal(select(session, 'run-1', 'other-attempt').length, 0)
assert.equal(select({ messages: [{ ...message, evidence_contract: { ...evidence, passed: false } }] }, 'run-1', 'attempt-1').length, 0)
assert.equal(select({ messages: [{ ...message, execution_verdict: { ...verdict, scientific_validation_success: false } }] }, 'run-1', 'attempt-1').length, 0)
assert.equal(select({ messages: [{ ...message, artifacts: { ...message.artifacts, python_plot: 'C:/private/wrong.png' } }] }, 'run-1', 'attempt-1').length, 1)
console.log('Verified gallery binding and multi-image selection: PASS')
