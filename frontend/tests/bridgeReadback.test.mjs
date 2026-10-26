import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'
import ts from 'typescript'

const source = readFileSync(new URL('../src/bridgeReadback.ts', import.meta.url), 'utf8')
const compiled = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.CommonJS },
}).outputText
const exports = {}
new Function('exports', compiled)(exports)
const { hasLiveModel, matchingReadback, sameBridgeAttachment } = exports

const status = {
  bridge_running: true, comsol_api_loaded: true, real_comsol: true,
  model_connected: true, model_tag: 'model_A',
}
const tree = {
  model_tag: 'model_A', command_success: true, model_state_verified: true,
  geometry: ['comp1:geom1'], physics: ['comp1:ht'],
}
const parameters = {
  model_tag: 'model_A', command_success: true, model_state_verified: true,
  parameters: { length: '10[mm]' },
}

test('live model requires a real connected COMSOL attachment', () => {
  assert.equal(hasLiveModel(status), true)
  for (const patch of [
    { bridge_running: false }, { comsol_api_loaded: false },
    { real_comsol: false }, { model_connected: false }, { model_tag: '' },
  ]) assert.equal(hasLiveModel({ ...status, ...patch }), false)
})

test('tree and parameters must be verified readbacks of the same model', () => {
  assert.deepEqual(matchingReadback(status, tree, parameters), {
    tree, parameters: { length: '10[mm]' },
  })
  for (const badTree of [
    { ...tree, model_tag: 'model_B' }, { ...tree, command_success: false },
    { ...tree, model_state_verified: false }, { ...tree, model_tag: undefined },
  ]) assert.equal(matchingReadback(status, badTree, parameters), null)
  for (const badParams of [
    { ...parameters, model_tag: 'model_B' },
    { ...parameters, command_success: false },
    { ...parameters, model_state_verified: false },
    { ...parameters, parameters: { length: 10 } },
  ]) assert.equal(matchingReadback(status, tree, badParams), null)
})

test('a model switch invalidates previous readback and no fake zeros are supplied', () => {
  assert.equal(matchingReadback({ ...status, model_tag: 'model_B' }, tree, parameters), null)
  assert.equal(matchingReadback(status, tree, { ...parameters, parameters: null }), null)
})

test('same model tag on a restarted Bridge is a different attachment', () => {
  const before = { ...status, process: { pid: 111, started_at: 'first', model_path: 'A.mph' } }
  assert.equal(sameBridgeAttachment(status, status), false)
  assert.equal(sameBridgeAttachment(before, { ...before, process: { ...before.process } }), true)
  assert.equal(sameBridgeAttachment(before, { ...before, process: { ...before.process, pid: 222 } }), false)
  assert.equal(sameBridgeAttachment(before, { ...before, process: { ...before.process, started_at: 'second' } }), false)
  assert.equal(sameBridgeAttachment(before, { ...before, process: { ...before.process, model_path: 'B.mph' } }), false)
  assert.equal(sameBridgeAttachment(before, { ...before, model_tag: 'model_B' }), false)
})
