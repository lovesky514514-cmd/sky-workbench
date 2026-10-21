import { useEffect, useState, type MouseEvent } from 'react'
import { createRoot } from 'react-dom/client'
import { emitTo, listen, type UnlistenFn } from '@tauri-apps/api/event'
import { getCurrentWindow } from '@tauri-apps/api/window'
import skyPet from './assets/sky-pet.png'
import './sky-pet.css'

const allowedStatus = new Set(['待命中', '思考中', '待确认', '运行中', '已暂停', '停止中', '已完成', '待核验', '出错了'])
const petWindow = getCurrentWindow()

function PetWindow() {
  const [status, setStatus] = useState('待命中')

  useEffect(() => {
    let active = true
    let unlisten: UnlistenFn | undefined
    void listen<string>('sky-pet-status', ({ payload }) => {
      if (allowedStatus.has(payload)) setStatus(payload)
    }).then((off) => {
      if (!active) { off(); return }
      unlisten = off
      void emitTo('main', 'sky-pet-ready')
    })
    return () => { active = false; unlisten?.() }
  }, [])

  async function hide() {
    await petWindow.hide()
    await emitTo('main', 'sky-pet-hidden')
  }

  function drag(event: MouseEvent) {
    if (event.button === 0) void petWindow.startDragging()
  }

  useEffect(() => {
    let active = true
    let unlisten: UnlistenFn | undefined
    void petWindow.onMoved(({ payload }) => {
      void emitTo('main', 'sky-pet-position', { x: payload.x, y: payload.y })
    }).then((off) => { if (active) unlisten = off; else off() })
    return () => { active = false; unlisten?.() }
  }, [])

  return <div className="sky-floating-pet" onMouseDown={drag} onContextMenu={(event) => { event.preventDefault(); void hide() }}>
    <img src={skyPet} alt="SKY" draggable={false} />
    <span className="sky-floating-status" role="status" aria-live="polite">{status}</span>
    <button className="sky-floating-hide" type="button" aria-label="隐藏桌宠 / Hide desktop pet" title="隐藏桌宠 / Hide desktop pet"
      onMouseDown={(event) => event.stopPropagation()} onClick={() => { void hide() }}>×</button>
  </div>
}

createRoot(document.getElementById('pet-root')!).render(<PetWindow />)
