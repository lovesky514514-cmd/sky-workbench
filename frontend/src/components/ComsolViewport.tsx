import { useEffect, useMemo, useRef, useState } from 'react'
import { AmbientLight, AxesHelper, Box3, BufferAttribute, BufferGeometry, Color, DirectionalLight, DoubleSide,
  Group, LineBasicMaterial, LineSegments, Mesh, MeshBasicMaterial, MeshStandardMaterial, MOUSE, PerspectiveCamera, Raycaster, Scene,
  Vector2, Vector3, WebGLRenderer } from 'three'
import { OrbitControls } from 'three/addons/controls/OrbitControls.js'
import type { ModelVisualization, Surface, Tetrahedra } from '../comsolTopology'
import { sliceTetrahedra } from '../comsolMeshSection'

type Mode = 'surface' | 'mesh' | 'both' | 'section'
type View = 'reset' | 'front' | 'top' | 'side'
const directions: Record<View, [number, number, number]> = { reset: [1.1, -1.6, 1.15], front: [0, -1, 0], top: [0, 0, 1], side: [1, 0, 0] }

export default function ComsolViewport({ manifest, surfaces, tetrahedra, temperature, zh }: {
  manifest: ModelVisualization; surfaces: Surface[]; tetrahedra?: Tetrahedra; temperature?: { min: number; max: number }; zh: boolean
}) {
  const host = useRef<HTMLDivElement>(null)
  const [mode, setMode] = useState<Mode>('surface')
  const [selection, setSelection] = useState<{ id: string; cell?: number } | null>(null)
  const [sectionPosition, setSectionPosition] = useState(50)
  const [pan, setPan] = useState(false)
  const [hidden, setHidden] = useState<string[]>([])
  const [error, setError] = useState(false)
  const actions = useRef<{ view: (view: View) => void; replaceSection: (items: Surface[]) => void;
    sync: (mode: Mode, id: string | null, hidden: string[], pan: boolean) => void } | null>(null)
  const isMesh = manifest.representation_type === 'MESH_READBACK'
  const dimension = manifest.dimension
  const sizes = Array.from({ length: dimension }, (_, axis) => manifest.bounds[2 * axis + 1] - manifest.bounds[2 * axis])
  const sectionZ = manifest.bounds[4] + sectionPosition / 100 * (manifest.bounds[5] - manifest.bounds[4])
  const section = useMemo(() => mode === 'section' && tetrahedra
    ? sliceTetrahedra(tetrahedra, sectionZ, Math.max(manifest.bounds[1] - manifest.bounds[0], manifest.bounds[3] - manifest.bounds[2]) * 1e-10) : [],
    [mode, tetrahedra, sectionZ, manifest.bounds])
  const visibleSurfaces = mode === 'section' ? section : surfaces
  const surface = visibleSurfaces.find(item => item.id === selection?.id)
  const domains = surface?.entity ? surface.kind === 'boundary' ? manifest.boundary_domains[surface.entity] ?? [] : [surface.entity] : []
  const physics = manifest.boundary_features.filter(item => surface?.kind === 'boundary' && item.ids.includes(surface.entity!))
  const materials = manifest.domain_materials.filter(item => item.ids.some(id => domains.includes(id)))

  useEffect(() => {
    const container = host.current!
    let renderer: WebGLRenderer
    try { renderer = new WebGLRenderer({ antialias: true }) }
    catch { setError(true); return }
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2))
    renderer.setClearColor(new Color('#f4f1e8'))
    renderer.domElement.setAttribute('aria-label', zh ? 'COMSOL 交互三维画布' : 'Interactive COMSOL canvas')
    container.appendChild(renderer.domElement)
    const scene = new Scene()
    const model = new Group()
    const camera = new PerspectiveCamera(40, 1, 0.01, 100)
    camera.up.set(0, 0, 1)
    const controls = new OrbitControls(camera, renderer.domElement)
    controls.enableDamping = false
    controls.mouseButtons.RIGHT = MOUSE.PAN
    const lit = new DirectionalLight(0xffffff, 2.1)
    scene.add(new AmbientLight(0xffffff, 1.25), lit, model)
    const material = new MeshStandardMaterial({ color: '#77a3a5', side: DoubleSide, roughness: 0.78,
      polygonOffset: true, polygonOffsetFactor: 1, polygonOffsetUnits: 1 })
    const highlight = material.clone()
    highlight.color.set('#e09849')
    const invisible = material.clone()
    invisible.colorWrite = false
    const field = new MeshBasicMaterial({ color: 0xffffff, vertexColors: true, side: DoubleSide,
      polygonOffset: true, polygonOffsetFactor: 1, polygonOffsetUnits: 1 })
    const lines = new LineBasicMaterial({ color: '#25464c' })
    const selectedLine = new LineBasicMaterial({ color: '#994319' })
    const attach = (surface: Surface) => {
      const geometry = new BufferGeometry().setAttribute('position', new BufferAttribute(surface.positions, 3))
      if (surface.colors) geometry.setAttribute('color', new BufferAttribute(surface.colors, 3))
      geometry.computeVertexNormals()
      const mesh = new Mesh<BufferGeometry, MeshBasicMaterial | MeshStandardMaterial>(geometry, material)
      mesh.userData.id = surface.id
      model.add(mesh)
      const wire = surface.edges ? new LineSegments(
        new BufferGeometry().setAttribute('position', new BufferAttribute(surface.edges, 3)), lines) : null
      if (wire) { wire.visible = false; model.add(wire) }
      if (wire && !surface.positions.length) wire.visible = true
      return { mesh, wire, surface }
    }
    const detach = ({ mesh, wire }: ReturnType<typeof attach>) => {
      model.remove(mesh)
      mesh.geometry.dispose()
      if (wire) { model.remove(wire); wire.geometry.dispose() }
    }
    const drawables = surfaces.map(attach)
    let sections: ReturnType<typeof attach>[] = []
    let sectionMode = false
    const box = new Box3().setFromObject(model)
    const extent = box.getSize(new Vector3()).length()
    const scale = extent > 0 ? 2 / extent : 1
    model.scale.setScalar(scale)
    model.position.copy(box.getCenter(new Vector3())).multiplyScalar(-scale)
    const axes = new AxesHelper(0.55)
    axes.position.set(-1.17, -0.88, -0.68)
    scene.add(axes)
    const draw = () => {
      lit.position.copy(camera.position).add(new Vector3(-0.2, 0.4, 0.5))
      renderer.render(scene, camera)
    }
    controls.addEventListener('change', draw)
    const fit = (view: View) => {
      const direction = dimension === 2 && view === 'reset' ? directions.top : directions[view]
      const distance = Math.max(3.5, 3.5 / camera.aspect)
      camera.position.set(...direction).normalize().multiplyScalar(distance)
      if (direction[2] === 1 && direction[0] === 0 && direction[1] === 0) camera.position.y = -1e-6
      controls.target.set(0, 0, 0)
      controls.update()
      draw()
    }
    const resize = new ResizeObserver(() => {
      const width = container.clientWidth, height = container.clientHeight
      if (!width || !height) return
      renderer.setSize(width, height)
      camera.aspect = width / height
      camera.updateProjectionMatrix()
      draw()
    })
    resize.observe(container)
    const raycaster = new Raycaster()
    raycaster.params.Line = { threshold: 0.018 }
    let start: [number, number] | null = null
    const down = (event: PointerEvent) => { start = event.button === 0 ? [event.clientX, event.clientY] : null }
    const up = (event: PointerEvent) => {
      if (event.button !== 0 || !start || Math.hypot(event.clientX - start[0], event.clientY - start[1]) > 4) return
      const bounds = renderer.domElement.getBoundingClientRect()
      raycaster.setFromCamera(new Vector2(2 * (event.clientX - bounds.left) / bounds.width - 1,
        1 - 2 * (event.clientY - bounds.top) / bounds.height), camera)
      const targets = (sectionMode ? sections : drawables).flatMap<Mesh | LineSegments>(({ mesh, wire, surface }) =>
        surface.positions.length ? (mesh.visible ? [mesh] : []) : (wire?.visible ? [wire] : []))
      const hit = raycaster.intersectObjects(targets, false)[0]
      const item = hit && (sectionMode ? sections : drawables).find(entry => entry.mesh === hit.object || entry.wire === hit.object)
      setSelection(item ? { id: item.surface.id, cell: item.surface.cells?.[hit.faceIndex ?? -1] } : null)
    }
    const contextLost = (event: Event) => { event.preventDefault(); setError(true) }
    renderer.domElement.addEventListener('pointerdown', down)
    renderer.domElement.addEventListener('pointerup', up)
    renderer.domElement.addEventListener('webglcontextlost', contextLost)
    actions.current = { view: fit, replaceSection(items) {
      sections.forEach(detach)
      sections = items.map(attach)
    }, sync(nextMode, id, hiddenIds, panning) {
      sectionMode = nextMode === 'section'
      controls.mouseButtons.LEFT = panning ? MOUSE.PAN : MOUSE.ROTATE
      for (const [isSection, items] of [[false, drawables], [true, sections]] as const) {
        for (const { mesh, wire, surface } of items) {
          const visible = isSection === sectionMode && !hiddenIds.includes(surface.id)
          mesh.visible = visible && surface.positions.length > 0
          mesh.material = nextMode === 'mesh' ? invisible : surface.colors ? field : surface.id === id ? highlight : material
          if (wire) {
            wire.visible = visible && (nextMode !== 'surface' || !surface.positions.length)
            wire.material = surface.id === id ? selectedLine : lines
          }
        }
      }
      draw()
    } }
    fit('reset')
    return () => {
      actions.current = null
      resize.disconnect()
      controls.dispose()
      renderer.domElement.removeEventListener('pointerdown', down)
      renderer.domElement.removeEventListener('pointerup', up)
      renderer.domElement.removeEventListener('webglcontextlost', contextLost)
      drawables.forEach(detach)
      sections.forEach(detach)
      axes.dispose()
      for (const entry of [material, highlight, invisible, field, lines, selectedLine]) entry.dispose()
      renderer.dispose()
      renderer.forceContextLoss()
      renderer.domElement.remove()
    }
  }, [dimension, surfaces, zh])

  useEffect(() => { actions.current?.replaceSection(section) }, [section, surfaces, zh])
  useEffect(() => { actions.current?.sync(mode, selection?.id ?? null, hidden, pan) }, [mode, selection, hidden, pan, section, surfaces, zh])
  const total = ['tet', 'pyr', 'prism', 'hex'].reduce((sum, key) => sum + (manifest.element_counts[key] ?? 0), 0)
  const canSection = Boolean(!temperature && tetrahedra && tetrahedra.cells.length / 5 === total &&
    total <= 100_000 && manifest.bounds[5] > manifest.bounds[4])
  return <div className="comsol-viewport">
    <div className="comsol-view-toolbar" role="group" aria-label={zh ? '模型视图' : 'Model view'}>
      {([['surface', '实体', 'Surface'], ['mesh', '网格', 'Mesh'], ['both', '叠加', 'Overlay']] as const).map(([key, cn, en]) =>
        <button key={key} disabled={key !== 'surface' && (!isMesh || Boolean(temperature))} aria-pressed={mode === key} onClick={() => {
          if (mode === 'section') setSelection(null)
          setMode(key)
        }}>{zh ? cn : en}</button>)}
      {canSection && <button aria-pressed={mode === 'section'} onClick={() => {
        setSelection(null)
        setMode('section')
        actions.current?.view('top')
      }}>{zh ? 'Z 剖面' : 'Z section'}</button>}
      <button aria-pressed={pan} onClick={() => setPan(!pan)}>{zh ? '平移' : 'Pan'}</button>
      {([['reset', '重置', 'Reset'], ['front', '正视', 'Front'], ['top', '俯视', 'Top'], ['side', '侧视', 'Side']] as const).map(([key, cn, en]) =>
        <button key={key} onClick={() => actions.current?.view(key)}>{zh ? cn : en}</button>)}
    </div>
    {mode === 'section' && <label className="comsol-section-position">Z = {Number(sectionZ.toPrecision(5))} {manifest.length_unit}
      <input type="range" min={1} max={99} value={sectionPosition} onChange={event => {
        setSelection(null)
        setSectionPosition(Number(event.target.value))
      }} />
    </label>}
    <div ref={host} className="comsol-webgl" />
    {temperature && <div className="comsol-temperature" aria-label={zh ? '温度色标' : 'Temperature scale'}>
      <strong>{zh ? '温度' : 'Temperature'} · t = {manifest.heat_field?.time_s} s</strong>
      <span>{temperature.min.toFixed(4)} K</span><i aria-hidden="true" /><span>{temperature.max.toFixed(4)} K</span>
    </div>}
    {error && <p role="alert">{zh ? '此窗口的 WebGL 渲染不可用。' : 'WebGL rendering is unavailable in this window.'}</p>}
    <div className="comsol-view-facts">
      <div><strong>{dimension}D · {sizes.map(value => Number(value.toPrecision(5))).join(' × ')} {manifest.length_unit}</strong>
        <span className="comsol-axes"><i>X</i> <i>Y</i> <i>Z</i></span></div>
      <small>{zh ? '拖动旋转，滚轮缩放，右键拖动平移' : 'Drag to rotate, wheel to zoom, right-drag to pan'}</small>
      <p>{isMesh ? `${manifest.mesh_tag} · ${manifest.element_counts.tet ?? 0} ${zh ? '四面体' : 'tetrahedra'} · ${total} ${zh ? '体单元' : 'volume elements'}` :
        manifest.geometry_encoding === 'boundary_curves' ? (zh ? '二维边界曲线 · COMSOL 边界 ID · 无网格' : '2D curves · COMSOL boundary IDs · no mesh') :
        manifest.geometry_encoding === 'boundary_triangles' ? (zh ? 'COMSOL 边界三角化 · 边界 ID 已核对 · 无网格' : 'COMSOL boundary tessellation · verified IDs · no mesh') :
        (zh ? 'COMSOL 对象表面 · 网格不可用 · 边界 ID 未确认' : 'COMSOL object surfaces · mesh unavailable · boundary IDs unconfirmed')}</p>
      {manifest.geometry_objects.length > 0 && <p>{zh ? '几何对象' : 'Geometry objects'} · {manifest.geometry_objects.join(', ')}</p>}
      <label>{zh ? '只读选择' : 'Read-only selection'} <select value={surface?.id ?? ''} onChange={event => setSelection(event.target.value ? { id: event.target.value } : null)}>
        <option value="">{zh ? '清除选择' : 'Clear selection'}</option>
        {visibleSurfaces.map(item => <option key={item.id} value={item.id}>{item.entity ? `${item.kind === 'boundary' ? (zh ? '边界' : 'Boundary') : (zh ? '域' : 'Domain')} #${item.entity}` : item.id}</option>)}
      </select></label>
      {surface && <div className="comsol-selection" aria-live="polite">
        <strong>{manifest.component_tag}/{manifest.geometry_tag} · {surface.entity ? `#${surface.entity}` : surface.id}</strong>
        {selection?.cell && <p>{zh ? '四面体连接表序号' : 'Tetrahedron connectivity index'} · {selection.cell}</p>}
        <p>{zh ? '相邻域' : 'Adjacent domains'} · {domains.join(', ') || '—'} · {zh ? '材料' : 'Materials'} · {materials.map(item => item.label).join(', ') || (zh ? '无材料节点映射' : 'No mapped material node')}</p>
        <p>{zh ? '边界条件' : 'Boundary features'} · {physics.map(item => `${item.tag} · ${item.label}`).join(', ') || '—'}</p>
        <button onClick={() => setHidden(previous => previous.includes(surface.id) ? previous.filter(id => id !== surface.id) : [...previous, surface.id])}>
          {hidden.includes(surface.id) ? (zh ? '显示选择' : 'Show selected') : (zh ? '隐藏选择' : 'Hide selected')}</button>
        <button onClick={() => setSelection(null)}>{zh ? '清除选择' : 'Clear selection'}</button>
      </div>}
    </div>
  </div>
}
