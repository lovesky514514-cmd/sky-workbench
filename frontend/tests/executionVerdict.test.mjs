// Render the actual badge for all sixteen product states, without a browser or new deps.
import { readFileSync } from 'node:fs'
import { createRequire } from 'node:module'
import assert from 'node:assert/strict'
import ts from 'typescript'
import React from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
const require = createRequire(import.meta.url)
const source = readFileSync(new URL('../src/ExecutionVerdictBadge.tsx', import.meta.url), 'utf8')
const compiled = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX } }).outputText
const exports = {}
new Function('require', 'exports', compiled)(require, exports)
for (let mask = 0; mask < 16; mask++) {
  const flags = [Boolean(mask & 8), Boolean(mask & 4), Boolean(mask & 2), Boolean(mask & 1)]
  const verdict = { run_id: 'fixture', command_success: flags[0], model_state_verified: flags[1], scientific_validation_success: flags[2], evidence_contract_passed: flags[3], completed: flags.every(Boolean) }
  const html = renderToStaticMarkup(React.createElement(exports.ExecutionVerdictBadge, { verdict, t: key => key }))
  assert.deepEqual([...html.matchAll(/[✓✕]/g)].map(x => x[0]), flags.map(x => x ? '✓' : '✕'))
  const display = exports.validationDisplay({ scope: 'solution', execution_verdict: verdict,
    command_success: !flags[0], model_state_verified: !flags[1], scientific_validation_success: !flags[2] })
  assert.deepEqual([display.command_success, display.model_state_verified, display.scientific_validation_success, display.evidence_contract_passed], flags)
  assert.equal(display.passed, flags.every(Boolean))
}
const precheck = { scope: 'precheck', passed: true, command_success: false }
assert.equal(exports.validationDisplay(precheck), precheck)
assert.equal(renderToStaticMarkup(React.createElement(exports.ExecutionVerdictBadge, { t: key => key })), '')
console.log('ExecutionVerdictBadge: 16/16 states and uncertified legacy PASS')
