export type ModelSource = { id: string; projectId: string } & (
  { kind: 'imported' | 'session' } | { kind: 'checkpoint'; runId: string; attemptId: string; index: number } |
  { kind: 'heat-result'; runId: string; attemptId: string }
)
type Assignment = { tag: string; label: string; ids: number[] }
export interface ModelVisualization {
  project_id: string
  model_identity: string
  source_kind: ModelSource['kind']
  source_fingerprint: string
  run_id?: string
  attempt_id?: string
  checkpoint_index?: number
  source_model_revision: string
  created_at: string
  verification_status: 'topology_verified'
  scientific_validation_passed: false
  representation_type: 'MODEL_READBACK' | 'MESH_READBACK'
  component_tag: string
  geometry_tag: string
  mesh_tag: string
  dimension: 2 | 3
  comsol_version: string
  length_unit: string
  bounds: number[]
  geometry_objects: string[]
  entity_counts?: number[]
  geometry_encoding?: 'boundary_curves' | 'boundary_triangles' | 'object_triangles'
  heat_field?: { dataset: string; time_s: number }
  element_counts: Record<string, number>
  boundary_domains: Record<string, number[]>
  boundary_features: Assignment[]
  domain_materials: Assignment[]
  artifact: { bytes: number; sha256: string }
}

export type Surface = { id: string; entity: number | null; kind: 'boundary' | 'domain' | 'object';
  positions: Float32Array; edges?: Float32Array; cells?: Uint32Array; colors?: Float32Array }
export type Tetrahedra = { positions: Float64Array; cells: Uint32Array }
const arities = [1, 2, 3, 4, 4, 5, 6, 8]
export const MAX_TOPOLOGY_BYTES = 32_000_000

export async function verifyTopology(manifest: ModelVisualization, source: ModelSource, raw: ArrayBuffer) {
  if (manifest.project_id !== source.projectId || manifest.source_kind !== source.kind || manifest.model_identity !== source.id
      || ('runId' in source && (manifest.run_id !== source.runId || manifest.attempt_id !== source.attemptId))
      || (source.kind === 'checkpoint' && manifest.checkpoint_index !== source.index)
      || ((source.kind === 'heat-result') !== Boolean(manifest.heat_field))
      || manifest.verification_status !== 'topology_verified' || manifest.scientific_validation_passed !== false
      || raw.byteLength !== manifest.artifact.bytes || raw.byteLength > MAX_TOPOLOGY_BYTES) {
    throw new Error('Model visualization identity or size mismatch')
  }
  const digest = Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256', raw)), b => b.toString(16).padStart(2, '0')).join('')
  if (digest !== manifest.artifact.sha256) throw new Error('Model visualization SHA-256 mismatch')
  return decodeTopology(raw, manifest)
}

export function decodeTopology(raw: ArrayBuffer, model: ModelVisualization) {
  const view = new DataView(raw)
  let offset = 0
  const word = () => { const n = view.getUint32(offset); offset += 4; return n }
  const coord = (wide: boolean) => {
    const n = wide ? view.getFloat64(offset) : view.getFloat32(offset)
    offset += wide ? 8 : 4
    if (!Number.isFinite(n) || Math.abs(n) > 1e30) throw new Error('Invalid topology coordinate')
    return n
  }
  const magic = word()
  if (raw.byteLength > MAX_TOPOLOGY_BYTES || magic !== (model.heat_field ? 0x53575632 : 0x53575631) ||
      ![2, 3].includes(model.dimension)) throw new Error('Invalid topology header')
  const metadata = word()
  if (metadata > 262144 || metadata + offset >= raw.byteLength) throw new Error('Invalid topology metadata length')
  offset += metadata
  const vertices = word()
  if (vertices > 250_000) throw new Error('Mesh vertex limit')
  const positions = new Float64Array(vertices * 3)
  for (let i = 0; i < vertices; i++) for (let axis = 0; axis < model.dimension; axis++) positions[i * 3 + axis] = coord(true)
  const groups = word()
  if (groups > 8 || Boolean(groups) !== Boolean(vertices) ||
      (groups ? model.representation_type !== 'MESH_READBACK' : model.representation_type !== 'MODEL_READBACK')) {
    throw new Error('Invalid topology representation')
  }
  const faces = new Map<number, { triangles: number[]; edges: number[] }>()
  const types = new Set<number>()
  let tetrahedra: Tetrahedra | undefined
  let elements = 0
  for (let group = 0; group < groups; group++) {
    const type = word()
    const count = word()
    elements += count
    if (!arities[type] || !count || types.has(type) || elements > 500_000 ||
        count * (1 + arities[type]) * 4 > view.byteLength - offset) throw new Error('Invalid mesh element block')
    types.add(type)
    if (type === 4) tetrahedra = { positions, cells: new Uint32Array(count * 5) }
    for (let i = 0; i < count; i++) {
      const entity = word()
      const indices = Array.from({ length: arities[type] }, word)
      if (!entity || indices.some(index => index >= vertices)) throw new Error('Invalid mesh entity or node')
      if (type === 4) tetrahedra!.cells.set([entity, ...indices], 5 * i)
      if (type !== 2 && type !== 3) continue
      if (!faces.has(entity)) faces.set(entity, { triangles: [], edges: [] })
      const face = faces.get(entity)!
      // COMSOL quadrilateral corners are ordered as a tensor product (0,1,2,3).
      const [a, b, c, d] = indices
      if (type === 2) {
        face.triangles.push(a, b, c)
        face.edges.push(a, b, b, c, c, a)
      } else {
        face.triangles.push(a, b, d, a, d, c)
        face.edges.push(a, b, b, d, d, c, c, a)
      }
    }
  }
  const surfaces = word()
  const encoding = model.geometry_encoding ?? 'object_triangles'
  const boundary = encoding !== 'object_triangles'
  const maximum = encoding === 'boundary_curves' ? 1024 : boundary ? 128 : 64
  const expected = boundary ? model.entity_counts?.[model.dimension - 1] : model.geometry_objects.length
  if (surfaces > maximum || (groups ? surfaces !== 0 : surfaces !== expected)) throw new Error('Invalid display surface count')
  const expand = (indices: number[]) => {
    const result = new Float32Array(indices.length * 3)
    indices.forEach((node, i) => {
      for (let axis = 0; axis < 3; axis++) result[3 * i + axis] = positions[3 * node + axis]
    })
    return result
  }
  const output: Surface[] = Array.from(faces, ([entity, value]) => ({
    id: String(entity), entity, kind: model.dimension === 3 ? 'boundary' : 'domain',
    positions: expand(value.triangles), edges: expand(value.edges),
  }))
  let triangles = 0
  for (let i = 0; i < surfaces; i++) {
    const count = word()
    const indexed = { id: String(i + 1), entity: i + 1, kind: 'boundary' as const }
    if (encoding === 'boundary_curves') {
      if (model.dimension !== 2 || count !== 65) throw new Error('Invalid planar boundary')
      const points = Float32Array.from({ length: 3 * count }, (_, index) => index % 3 === 2 ? 0 : coord(true))
      const edges = new Float32Array(6 * (count - 1))
      for (let point = 0; point < count - 1; point++) {
        edges.set(points.subarray(3 * point, 3 * point + 6), 6 * point)
      }
      output.push({ ...indexed, positions: new Float32Array(), edges })
      continue
    }
    triangles += count
    if (!count || triangles > 80_000) throw new Error('Invalid tessellation size')
    output.push({ ...(boundary ? indexed : { id: model.geometry_objects[i], entity: null, kind: 'object' as const }),
      positions: Float32Array.from({ length: 9 * count }, () => coord(false)) })
  }
  let temperature: { min: number; max: number } | undefined
  if (model.heat_field) {
    if (!tetrahedra || word() !== vertices) throw new Error('Temperature sample count mismatch')
    let min = Infinity, max = -Infinity
    const values = Float64Array.from({ length: vertices }, () => {
      const value = coord(true)
      if (value <= 0 || value >= 1e7) throw new Error('Invalid temperature value')
      min = Math.min(min, value)
      max = Math.max(max, value)
      return value
    })
    temperature = { min, max }
    const palette = new Float32Array(vertices * 3)
    for (let i = 0; i < vertices; i++) {
      const f = max === min ? 0.5 : (values[i] - min) / (max - min)
      palette.set([0.018 + 0.89 * f, 0.12 + 0.16 * (1 - Math.abs(2 * f - 1)), 0.74 - 0.72 * f], 3 * i)
    }
    for (const surface of output) {
      const indices = faces.get(surface.entity!)!.triangles
      surface.colors = Float32Array.from({ length: indices.length * 3 },
        (_, i) => palette[3 * indices[Math.floor(i / 3)] + i % 3])
    }
  }
  if (offset !== view.byteLength || !output.length) throw new Error('Incomplete display topology')
  return { surfaces: output, tetrahedra, temperature }
}
