import { useEffect, useRef, useState } from 'react'
import { isTauri } from '@tauri-apps/api/core'
import { emitTo, listen, type UnlistenFn } from '@tauri-apps/api/event'
import { PhysicalPosition } from '@tauri-apps/api/dpi'
import { availableMonitors } from '@tauri-apps/api/window'
import { WebviewWindow } from '@tauri-apps/api/webviewWindow'
import { skyPetStatus, type SkyPetInput } from '../skyPetStatus'
import { petPosition, type PetPosition } from '../skyPetPosition'
import { translate, type Locale } from '../i18n'

const visibilityKey = 'sky-workbench-pet-visible-v1'
const positionKey = 'sky-workbench-pet-position-v1'

function savedPosition(): PetPosition | null {
  try {
    const value: unknown = JSON.parse(localStorage.getItem(positionKey) ?? 'null')
    if (value && typeof value === 'object' && 'x' in value && 'y' in value &&
      typeof value.x === 'number' && typeof value.y === 'number') return { x: value.x, y: value.y }
  } catch { /* A corrupt preference must never interrupt the Workbench. */ }
  return null
}

export function SkyPetController({ state, locale }: { state: SkyPetInput; locale: Locale }) {
  const [visible, setVisible] = useState(() => localStorage.getItem(visibilityKey) === 'true')
  const [available, setAvailable] = useState(isTauri())
  const status = skyPetStatus(state)
  const statusRef = useRef(status)
  statusRef.current = status

  // Only the real desktop shell owns the optional overlay. Browser previews
  // never simulate a floating pet or a scientific status source.
  useEffect(() => {
    if (!isTauri()) return
    let active = true
    const unlisteners: UnlistenFn[] = []
    void Promise.all([
      listen('sky-pet-ready', () => { void emitTo('sky-pet', 'sky-pet-status', statusRef.current) }),
      listen('sky-pet-hidden', () => setVisible(false)),
      listen<PetPosition>('sky-pet-position', ({ payload }) => {
        if (Number.isFinite(payload?.x) && Number.isFinite(payload?.y)) {
          localStorage.setItem(positionKey, JSON.stringify({ x: payload.x, y: payload.y }))
        }
      }),
    ]).then((listeners) => { if (active) unlisteners.push(...listeners); else listeners.forEach((off) => off()) })
      .catch(() => { if (active) setAvailable(false) })
    return () => { active = false; unlisteners.forEach((off) => off()) }
  }, [])

  useEffect(() => {
    if (!isTauri()) return
    localStorage.setItem(visibilityKey, String(visible))
    void (async () => {
      const pet = await WebviewWindow.getByLabel('sky-pet')
      if (!pet) { setAvailable(false); return }
      if (!visible) { await pet.hide(); return }
      const [size, monitors] = await Promise.all([pet.outerSize(), availableMonitors()])
      const position = petPosition(savedPosition(), monitors.map((monitor) => monitor.workArea), size.width, size.height)
      if (position) await pet.setPosition(new PhysicalPosition(position.x, position.y))
      await pet.show()
      await emitTo('sky-pet', 'sky-pet-status', statusRef.current)
    })().catch(() => setAvailable(false))
  }, [visible])

  useEffect(() => { if (visible && available) void emitTo('sky-pet', 'sky-pet-status', status).catch(() => setAvailable(false)) }, [status, visible, available])

  if (!isTauri()) return null
  return <button className="sky-pet-toggle" type="button" aria-pressed={visible} disabled={!available}
    title={available ? translate(locale, visible ? 'Hide desktop pet' : 'Show desktop pet') : translate(locale, 'Desktop pet unavailable')}
    onClick={() => setVisible((value) => !value)}>
    {translate(locale, visible ? 'Hide desktop pet' : 'Show desktop pet')}
  </button>
}
