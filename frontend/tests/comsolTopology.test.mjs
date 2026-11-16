import assert from 'node:assert/strict'
import { createHash } from 'node:crypto'
import { readFileSync } from 'node:fs'
import test from 'node:test'
import ts from 'typescript'
import { JSDOM } from 'jsdom'
import { MOUSE, PerspectiveCamera } from 'three'
import { OrbitControls } from 'three/addons/controls/OrbitControls.js'

const source = readFileSync(new URL('../src/comsolTopology.ts', import.meta.url), 'utf8')
const api = {}
new Function('exports', ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS } }).outputText)(api)

function mesh(entity = 3) {
  const header = Buffer.alloc(13)
  header.write('SWV1')
  header.writeUInt32BE(1, 4)
  header.writeUInt32BE(3, 9)
  const vertices = Buffer.alloc(72)
  vertices.writeDoubleBE(2, 24)
  vertices.writeDoubleBE(1, 56)
  const cells = Buffer.alloc(32)
  ;[1, 2, 1, entity, 0, 1, 2, 0].forEach((value, i) => cells.writeUInt32BE(value, i * 4))
  const raw = Uint8Array.from(Buffer.concat([header, vertices, cells])).buffer
  return { raw, manifest: { project_id: 'p', model_identity: 'mph', source_kind: 'imported',
    dimension: 3, verification_status: 'topology_verified', scientific_validation_passed: false,
    representation_type: 'MESH_READBACK', geometry_objects: ['solid'],
    artifact: { bytes: raw.byteLength, sha256: createHash('sha256').update(Buffer.from(raw)).digest('hex') } } }
}

test('an unsolved topology keeps the COMSOL boundary id and actual element edges', async () => {
  const { raw, manifest } = mesh()
  const { surfaces: [surface] } = await api.verifyTopology(manifest, { kind: 'imported', id: 'mph', projectId: 'p' }, raw)
  assert.equal(surface.entity, 3)
  assert.deepEqual([...surface.positions], [0, 0, 0, 2, 0, 0, 0, 1, 0])
  assert.equal(surface.edges.length, 18)
})

test('stale identity, corrupt hash, incorrect length and invalid entity do not render', async () => {
  const { raw, manifest } = mesh()
  const current = { kind: 'imported', id: 'mph', projectId: 'p' }
  await assert.rejects(api.verifyTopology(manifest, { ...current, id: 'other' }, raw), /identity/)
  await assert.rejects(api.verifyTopology(manifest, current, raw.slice(0, -1)), /size/)
  const changed = raw.slice(0)
  new Uint8Array(changed)[13] ^= 1
  await assert.rejects(api.verifyTopology(manifest, current, changed), /SHA-256/)
  const zero = mesh(0)
  await assert.rejects(api.verifyTopology(zero.manifest, current, zero.raw), /entity/)
})

test('an active checkpoint for another attempt or mesh index is rejected', async () => {
  const { raw, manifest } = mesh()
  const checked = { ...manifest, source_kind: 'checkpoint', run_id: 'run', attempt_id: 'current', checkpoint_index: 2 }
  const current = { kind: 'checkpoint', id: 'mph', projectId: 'p', runId: 'run', attemptId: 'current', index: 2 }
  assert.equal((await api.verifyTopology(checked, current, raw)).surfaces[0].entity, 3)
  await assert.rejects(api.verifyTopology(checked, { ...current, attemptId: 'old' }, raw), /identity/)
  await assert.rejects(api.verifyTopology(checked, { ...current, index: 1 }, raw), /identity/)
})

test('a section intersects real tetrahedra and retains cell and domain identity', () => {
  const slice = {}
  const code = readFileSync(new URL('../src/comsolMeshSection.ts', import.meta.url), 'utf8')
  new Function('exports', ts.transpileModule(code, { compilerOptions: { module: ts.ModuleKind.CommonJS } }).outputText)(slice)
  const nodes = {
    positions: new Float64Array([0, 0, 0, 2, 0, 0, 0, 2, 0, 0, 0, 2]),
    cells: new Uint32Array([8, 0, 1, 2, 3]),
  }
  const [section] = slice.sliceTetrahedra(nodes, 1, 1e-9)
  assert.equal(section.entity, 8)
  assert.deepEqual([...section.cells], [1])
  assert.equal(section.edges.length, 18)
  assert.deepEqual([...section.positions].filter((_, i) => i % 3 === 2), [1, 1, 1])
  assert.deepEqual(slice.sliceTetrahedra(nodes, 3, 1e-9), [])
})

test('right-button drag translates both camera and target without rotating the model', () => {
  const viewportSource = readFileSync(new URL('../src/components/ComsolViewport.tsx', import.meta.url), 'utf8')
  assert.match(viewportSource, /controls\.mouseButtons\.RIGHT = MOUSE\.PAN/)
  const page = new JSDOM('<canvas></canvas>')
  try {
    const { document, MouseEvent } = page.window
    const canvas = document.querySelector('canvas')
    Object.defineProperty(canvas, 'clientHeight', { value: 300 })
    canvas.setPointerCapture = canvas.releasePointerCapture = () => {}
    const camera = new PerspectiveCamera(40, 2, 0.01, 100)
    camera.position.set(2, 3, 4)
    const controls = new OrbitControls(camera, canvas)
    controls.mouseButtons.RIGHT = MOUSE.PAN
    const direction = camera.position.clone().sub(controls.target)
    const dispatch = (target, type, x, y) => {
      const event = new MouseEvent(type, { button: 2, clientX: x, clientY: y, bubbles: true })
      Object.defineProperty(event, 'pointerId', { value: 1 })
      target.dispatchEvent(event)
    }
    dispatch(canvas, 'pointerdown', 200, 100)
    dispatch(document, 'pointermove', 245, 120)
    dispatch(document, 'pointerup', 245, 120)
    assert.ok(controls.target.length() > 0)
    assert.ok(camera.position.clone().sub(controls.target).distanceTo(direction) < 1e-10)
    controls.dispose()
  } finally {
    page.window.close()
  }
})
