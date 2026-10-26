import { useEffect, useRef, useState } from 'react'
import { api } from '../api'
import { captureIsWindowOnly, captureStatus, stopCapture } from '../externalWindowPolicy'
import type { Locale } from '../i18n'

type Adapter = { engine: string; installed: boolean; readback: string; execute: string }
type PreviewState = 'idle' | 'requesting' | 'previewing' | 'not-allowed' | 'wrong-surface' | 'unavailable'

type Props = { locale: Locale; onOpenSettings: () => void }

export function ExternalWindowPreview({ locale, onOpenSettings }: Props) {
  const zh = locale === 'zh-CN'
  const copy = (cn: string, en: string) => zh ? cn : en
  const [adapters, setAdapters] = useState<Adapter[] | null>(null)
  const [state, setState] = useState<PreviewState>('idle')
  const video = useRef<HTMLVideoElement>(null)
  const stream = useRef<MediaStream | null>(null)
  const mounted = useRef(false)

  // Discovery is informational. Neither installed software nor a passing
  // engine handshake is a model readback or a scientific verdict.
  useEffect(() => {
    let active = true
    void api.simulationAdapters().then((result) => {
      if (active) setAdapters(result.adapters)
    }).catch(() => {
      if (active) setAdapters([])
    })
    return () => { active = false }
  }, [])

  const release = () => {
    stopCapture(stream.current)
    stream.current = null
    if (video.current) video.current.srcObject = null
  }

  // Leaving the model page, changing tasks, or minimizing the app must end
  // capture. Frames are never copied to Runtime, files, or an AI provider.
  useEffect(() => {
    mounted.current = true
    const onHidden = () => {
      if (document.hidden && stream.current) {
        release()
        setState('idle')
      }
    }
    document.addEventListener('visibilitychange', onHidden)
    return () => {
      mounted.current = false
      document.removeEventListener('visibilitychange', onHidden)
      release()
    }
  }, [])

  const stop = () => {
    release()
    setState('idle')
  }

  const start = async () => {
    if (state === 'requesting' || state === 'previewing') return
    if (!navigator.mediaDevices?.getDisplayMedia) {
      setState('unavailable')
      return
    }
    setState('requesting')
    try {
      // The system picker is mandatory. Reject monitor/tab capture even if
      // the picker offers it; this workspace promises a single window only.
      const selected = await navigator.mediaDevices.getDisplayMedia({ video: true, audio: false })
      const track = selected.getVideoTracks()[0]
      if (!mounted.current || !track) {
        stopCapture(selected)
        if (mounted.current) setState('unavailable')
        return
      }
      if (!captureIsWindowOnly(track.getSettings())) {
        stopCapture(selected)
        setState('wrong-surface')
        return
      }
      stream.current = selected
      track.addEventListener('ended', stop, { once: true })
      if (video.current) {
        video.current.srcObject = selected
        setState('previewing')
        await video.current.play()
      }
      if (!mounted.current) release()
    } catch (error) {
      release()
      if (mounted.current) setState(captureStatus(error instanceof DOMException ? error.name : 'unknown'))
    }
  }

  const message: Record<PreviewState, string> = {
    idle: copy('选择一个已打开的软件窗口，仅在本机查看画面。', 'Choose an open software window for a local-only view.'),
    requesting: copy('等待系统窗口选择…', 'Waiting for the system window picker…'),
    previewing: copy('只读窗口预览 · 未验证', 'Read-only window preview · unverified'),
    'not-allowed': copy('未选择窗口或未授予屏幕共享权限。', 'No window was chosen or permission was denied.'),
    'wrong-surface': copy('请只选择单个软件窗口；整个屏幕和浏览器标签不会显示。', 'Choose one software window; full screens and browser tabs are rejected.'),
    unavailable: copy('此桌面环境无法预览窗口；可继续使用软件检测。', 'Window preview is unavailable in this desktop environment; discovery still works.'),
  }
  const shortState: Record<PreviewState, string> = {
    idle: copy('未开始', 'Not started'),
    requesting: copy('等待选择', 'Waiting for selection'),
    previewing: copy('预览中 · 未验证', 'Previewing · unverified'),
    'not-allowed': copy('未获许可', 'Not allowed'),
    'wrong-surface': copy('来源不符', 'Wrong source'),
    unavailable: copy('不可用', 'Unavailable'),
  }

  return <section className="warm-page warm-science-page warm-external-page">
    <div className="warm-page-heading">
      <div><h1>{copy('外部软件窗口', 'External software window')}</h1>
        <p>{copy('预览由你选择的窗口；不会读取模型结构或向 AI 发送画面。',
          'Preview a window you choose; no model structure is read and no frames are sent to AI.')}</p></div>
      <span className="warm-status-pill">{copy('未验证预览', 'Unverified preview')}</span>
    </div>
    <div className="warm-science-layout">
      <div className="warm-viewport warm-external-viewport">
        <header><strong>{copy('本机窗口画面', 'Local window view')}</strong><span>{shortState[state]}</span></header>
        <div className="warm-external-screen">
          <video ref={video} autoPlay muted playsInline aria-label={copy('外部软件实时窗口预览', 'Live external software window preview')}
            className={state === 'previewing' ? 'is-visible' : ''} />
          {state !== 'previewing' && <p>{message[state]}</p>}
        </div>
        <footer><span>{copy('画面不保存、不上传、不作为科学证据', 'Frames are not saved, uploaded or used as scientific evidence')}</span>
          {state === 'previewing'
            ? <button type="button" onClick={stop}>{copy('停止预览', 'Stop preview')}</button>
            : <button type="button" onClick={() => void start()} disabled={state === 'requesting'}>{copy('选择窗口预览', 'Choose window')}</button>}</footer>
      </div>
      <aside className="warm-science-inspector warm-external-inspector">
        <h2>{copy('本机软件', 'Installed software')}</h2>
        <small>{copy('安装状态 · 非模型认证', 'DISCOVERY · NOT MODEL CERTIFICATION')}</small>
        <div className="warm-external-engines">
          {adapters === null && <p>{copy('正在读取适配状态…', 'Reading adapter status…')}</p>}
          {adapters?.length === 0 && <p>{copy('适配状态不可用', 'Adapter status unavailable')}</p>}
          {adapters?.filter((item) => item.engine !== 'COMSOL').map((item) =>
            <div key={item.engine}><strong>{item.engine}</strong><span>{item.installed
              ? copy('已检测 · 模型未同步', 'Detected · model not synchronized')
              : copy('未检测到', 'Not detected')}</span></div>)}
        </div>
        <p className="warm-review-note">{copy('窗口画面不能提供对象 ID、参数、边界选择或求解证据。真实模型同步需要该软件的结构化接口。',
          'A window image cannot provide object IDs, parameters, boundary selections or solver evidence. Model sync requires that vendor’s structured API.')}</p>
        <button type="button" onClick={onOpenSettings}>{copy('查看软件检测与连接', 'Open software detection and connection')}</button>
      </aside>
    </div>
  </section>
}
