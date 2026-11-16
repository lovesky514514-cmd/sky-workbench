import type { Surface, Tetrahedra } from './comsolTopology'

const tetrahedronEdges = [[0, 1], [0, 2], [0, 3], [1, 2], [1, 3], [2, 3]]
const cellStride = 5 // 一个域和四个顶点 / One domain and four vertices.

export function sliceTetrahedra({ positions, cells }: Tetrahedra, z: number, xyTolerance: number): Surface[] {
  const domains = new Map<number, { triangles: number[]; edges: number[]; indices: number[] }>()
  for (let offset = 0; offset < cells.length; offset += cellStride) {
    const domain = cells[offset]
    const nodes = cells.subarray(offset + 1, offset + cellStride)
    const polygon: [number, number, number][] = []
    for (const [first, second] of tetrahedronEdges) {
      const a = nodes[first] * 3
      const b = nodes[second] * 3
      const az = positions[a + 2]
      const bz = positions[b + 2]
      // 半开区间避免共享面重复 / The half-open interval avoids duplicate shared faces.
      if ((az <= z) === (bz <= z)) continue
      const fraction = (z - az) / (bz - az)
      const x = positions[a] + fraction * (positions[b] - positions[a])
      const y = positions[a + 1] + fraction * (positions[b + 1] - positions[a + 1])
      if (!polygon.some(point => Math.hypot(point[0] - x, point[1] - y) <= xyTolerance)) polygon.push([x, y, z])
    }
    if (polygon.length < 3) continue
    const x = polygon.reduce((sum, point) => sum + point[0], 0) / polygon.length
    const y = polygon.reduce((sum, point) => sum + point[1], 0) / polygon.length
    polygon.sort((a, b) => Math.atan2(a[1] - y, a[0] - x) - Math.atan2(b[1] - y, b[0] - x))
    if (!domains.has(domain)) domains.set(domain, { triangles: [], edges: [], indices: [] })
    const section = domains.get(domain)!
    for (let i = 1; i + 1 < polygon.length; i++) {
      section.triangles.push(...polygon[0], ...polygon[i], ...polygon[i + 1])
      section.indices.push(offset / cellStride + 1) // 原始单元序号从 1 开始 / Source cells are numbered from 1.
    }
    for (let i = 0; i < polygon.length; i++) section.edges.push(...polygon[i], ...polygon[(i + 1) % polygon.length])
  }
  return Array.from(domains, ([entity, section]) => ({
    id: `domain-${entity}`, entity, kind: 'domain',
    positions: Float32Array.from(section.triangles), edges: Float32Array.from(section.edges), cells: Uint32Array.from(section.indices),
  }))
}
