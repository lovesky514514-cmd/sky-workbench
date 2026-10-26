import { hasLiveModel, type ModelReadback } from '../bridgeReadback'
import { translate, type Locale } from '../i18n'
import type { JavaBridgeEvent, JavaBridgeStatus } from '../types'

type Props = {
  locale: Locale
  bridge: JavaBridgeStatus
  readback: ModelReadback | null
  loading: boolean
  events: JavaBridgeEvent[]
  onRefresh: () => void
  onStart: () => void
  onStop: () => void
}

function tags(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((item): item is string => typeof item === 'string') : []
}

function StatusFact({ label, value, status }: { label: string; value: string; status: string }) {
  return <div className="status-fact">
    <span><strong>{label}</strong><small>{value}</small></span>
    <em className={`status-text ${status.toLowerCase().replaceAll(' ', '-')}`}>{status}</em>
  </div>
}

// Bridge history is global; only the matching tree and parameter pair is
// current-model readback. Neither one certifies a selected scientific task.
export function LiveComsolView({ locale, bridge, readback, loading, events, onRefresh, onStart, onStop }: Props) {
  const t = (source: string) => translate(locale, source)
  const processState = String(bridge.process?.state ?? (bridge.bridge_running ? 'ready' : 'idle'))
  const connected = hasLiveModel(bridge)
  const tree = readback?.tree
  const geometry = tags(tree?.geometry)
  const physics = tags(tree?.physics)
  const mesh = tags(tree?.mesh)
  const studies = tags(tree?.studies)
  const readbackStatus = readback ? 'verified' : loading ? 'loading' : 'unavailable'
  const shownEvents = events.slice(-20).reverse()

  return <div className="inspector-body live-comsol">
    <div className="inspector-section-title">
      <span>{t('COMSOL live state')}</span>
      <div className="live-actions">
        <button onClick={onRefresh}>{t('Refresh')}</button>
        {bridge.bridge_running
          ? <button onClick={onStop}>{t('Disconnect')}</button>
          : <button onClick={onStart} disabled={processState === 'starting'}>
              {processState === 'starting' ? t('Connecting…') : t('Connect')}
            </button>}
      </div>
    </div>
    <p className="muted-block">{locale === 'zh-CN'
      ? '这是本机 COMSOL 的只读模型状态，不是窗口画面，也不证明当前任务的科学结果。'
      : 'This is read-only COMSOL model state, not window pixels or scientific certification of the selected task.'}</p>
    <div className="live-status-grid">
      <StatusFact label="API" value={bridge.comsol_version || t('Not loaded')}
        status={bridge.comsol_api_loaded ? 'loaded' : 'unavailable'} />
      <StatusFact label="Java Bridge" value={bridge.base_url || 'localhost'}
        status={bridge.bridge_running ? 'connected' : processState} />
      <StatusFact label={t('Model')} value={connected ? bridge.model_tag : t('No model')}
        status={readbackStatus} />
      <StatusFact label={t('Changes')} value={bridge.detection_method || 'unavailable'}
        status={readback ? 'live' : 'unavailable'} />
      <StatusFact label={t('Geometry')} value={geometry.join(', ') || t('No readback')}
        status={geometry.length ? 'built' : readbackStatus} />
      <StatusFact label={t('Physics')} value={physics.join(', ') || t('No readback')}
        status={physics.length ? 'verified' : readbackStatus} />
      <StatusFact label={t('Mesh')} value={mesh.join(', ') || t('No readback')}
        status={mesh.length ? 'built' : readbackStatus} />
      <StatusFact label={t('Study')} value={studies.join(', ') || t('No readback')}
        status={studies.length ? 'ready' : readbackStatus} />
      <StatusFact label={t('Scientific Validation')} value={t('No validation report recorded.')}
        status="unavailable" />
    </div>
    <div className="metric-grid live-metrics">
      <div><strong>{readback ? Object.keys(readback.parameters).length : '—'}</strong>
        <span>{t('real model parameters')}</span></div>
      <div><strong>{bridge.poll_interval_ms ? `${bridge.poll_interval_ms} ms` : '—'}</strong>
        <span>{t('polling fallback interval')}</span></div>
    </div>
    <div className="inspector-section-title"><span>{t('Recent activity')}</span>
      <small>{shownEvents.length} / 100 {t('shown')}</small></div>
    <p className="muted-block">{locale === 'zh-CN'
      ? '以下是 Bridge 历史事件，可能属于此前打开的模型。'
      : 'Bridge history may include events from a previously attached model.'}</p>
    <div className="live-activity">
      {!shownEvents.length && <p className="muted-block">{t('Connect the Bridge to receive real COMSOL events.')}</p>}
      {shownEvents.map((event, index) => <article key={`${event.sequence}-${event.created_at ?? index}`}>
        <time>{event.created_at ? new Date(event.created_at).toLocaleTimeString() : '—'}</time>
        <span className={`event-type-chip ${event.event_type}`}>{event.event_type.replaceAll('_', ' ')}</span>
        <div><strong>{event.target || event.phase} · {event.status}</strong>
          <small>{event.old_value != null || event.new_value != null
            ? `${event.old_value || '∅'} → ${event.new_value || '∅'}`
            : event.message || event.detail}</small>
          {event.percent != null && <progress value={event.percent} max={100} />}
        </div>
      </article>)}
    </div>
  </div>
}
