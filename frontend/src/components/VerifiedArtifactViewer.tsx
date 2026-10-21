import { useEffect, useMemo, useState } from 'react'
import { api } from '../api'
import { translate, type Locale } from '../i18n'
import type { Session } from '../types'
import { WorkspaceIcon } from './WorkspaceIcon'
import { verifiedImageArtifacts } from './verifiedImageArtifacts'
import { exportAcademicFigure } from './academicFigure'

export function VerifiedArtifactViewer({ locale, session, presentation = 'legacy', currentRunId, currentAttemptId, onIntegrityLost }: {
  locale: Locale; session: Session | null; presentation?: 'legacy' | 'gallery'; currentRunId?: string; currentAttemptId?: string
  onIntegrityLost?: (runId: string, attemptId: string) => void
}) {
  const t = (value: string) => translate(locale, value)
  const images = useMemo(() => verifiedImageArtifacts(session, currentRunId, currentAttemptId), [session, currentRunId, currentAttemptId])
  const [index, setIndex] = useState(0)
  const [state, setState] = useState<'loading' | 'verified' | 'lost' | 'unavailable'>('unavailable')
  const [source, setSource] = useState('')
  const [zoomOpen, setZoomOpen] = useState(false)
  const [exportError, setExportError] = useState(false)
  const artifact = images[index] ?? images[0]

  useEffect(() => { setIndex(0); setZoomOpen(false) }, [session?.id, currentRunId, currentAttemptId])

  // Presentation export is deliberately separate from the evidence artifact.
  // A failed canvas export only changes this local UI, never scientific state.
  async function exportFigure() {
    if (state !== 'verified' || !artifact || !source) return
    setExportError(false)
    try {
      await exportAcademicFigure({ source, title: t(artifact.name), quantity: t(artifact.quantity), unit: artifact.unit,
        filename: artifact.filename, runId: artifact.message.run_id as string, originalSha256: artifact.record.sha256 })
    } catch { setExportError(true) }
  }

  useEffect(() => {
    let current = ''
    let active = true
    if (!artifact) { setState('unavailable'); setSource(''); return () => undefined }
    setState('loading'); setSource('')
    void api.artifactObjectUrl(artifact.message.run_id as string, artifact.filename).then((value) => {
      if (!active) { URL.revokeObjectURL(value); return }
      current = value; setSource(value); setState('verified')
    }).catch(() => {
      if (!active) return
      // Raw API failures can contain private absolute paths. They belong in
      // diagnostics, never inside a shared scientific-results screenshot.
      setSource(''); setState('lost'); setZoomOpen(false)
      // The parent must revoke its outer Verified badge too. This callback
      // carries the exact current attempt so a late failure cannot taint B.
      if (currentRunId && currentAttemptId) onIntegrityLost?.(currentRunId, currentAttemptId)
    })
    return () => { active = false; if (current) URL.revokeObjectURL(current) }
  }, [artifact?.filename, artifact?.message.run_id, artifact?.record.sha256])

  useEffect(() => {
    if (!zoomOpen) return
    const escape = (event: KeyboardEvent) => { if (event.key === 'Escape') setZoomOpen(false) }
    window.addEventListener('keydown', escape)
    return () => window.removeEventListener('keydown', escape)
  }, [zoomOpen])

  if (!artifact || state === 'unavailable') return <div className="artifact-unavailable"><WorkspaceIcon name="results" size={28} /><h2>{t('No verified result artifact available.')}</h2><p>{t('A result image is shown only after a completed run publishes frozen evidence.')}</p></div>
  if (state === 'lost') return <div className="artifact-integrity-lost" role="alert"><WorkspaceIcon name="warning" size={28} /><h2>{t('Evidence integrity lost')}</h2><p>{t('The artifact bytes no longer match the frozen SHA-256. Certified/verified display is disabled.')}</p></div>

  if (presentation === 'gallery') return <div className="verified-artifact-viewer warm-gallery" tabIndex={0} onKeyDown={(event) => { if (event.key === 'ArrowRight') setIndex((value) => (value + 1) % images.length); if (event.key === 'ArrowLeft') setIndex((value) => (value - 1 + images.length) % images.length) }}>
    <header className="warm-gallery-title"><div><span>{t(state === 'verified' ? 'Verified artifact intact' : 'Verifying artifact integrity…')}</span><h2>{t(artifact.name)}</h2><small>{artifact.quantity ? `${t(artifact.quantity)} · ${artifact.unit}` : artifact.filename}</small></div><div className="warm-gallery-actions"><button onClick={() => void exportFigure()} disabled={state !== 'verified' || !source}>{t('Export figure sheet')}</button><button onClick={() => setZoomOpen(true)} disabled={!source} aria-label={t('Enlarge image')}>{t('Enlarge image')} ↗</button></div></header>
    <div className="warm-gallery-stage"><button className="gallery-previous" onClick={() => setIndex((value) => (value - 1 + images.length) % images.length)} disabled={images.length < 2} aria-label={t('Previous result')}>‹</button>{source ? <img src={source} alt={t(artifact.name)} onClick={() => setZoomOpen(true)} /> : <span>{t('Verifying artifact integrity…')}</span>}<button className="gallery-next" onClick={() => setIndex((value) => (value + 1) % images.length)} disabled={images.length < 2} aria-label={t('Next result')}>›</button></div>
    <footer className="warm-gallery-footer"><strong>{t(artifact.name)}</strong><span>{index + 1} / {images.length} · {artifact.filename}</span><small>{t('Verified output from current run')} · {t('Figure sheet is a presentation copy, not evidence')}</small>{exportError && <span role="alert">{t('Figure export unavailable')}</span>}</footer>
    {images.length > 1 && <nav className="warm-gallery-pages" aria-label={t('Result pages')}>{images.map((item, page) => <button key={`${item.key}-${page}`} className={page === index ? 'is-current' : ''} onClick={() => setIndex(page)} aria-label={`${t('Result page')} ${page + 1}: ${t(item.name)}`}>{page + 1}</button>)}</nav>}
    <details className="warm-gallery-provenance"><summary>{t('Artifact details')}</summary><dl><div><dt>{t('Result name')}</dt><dd>{artifact.filename}</dd></div><div><dt>{t('Run ID')}</dt><dd><code>{artifact.message.run_id}</code></dd></div><div><dt>SHA-256</dt><dd><code>{artifact.record.sha256}</code></dd></div></dl></details>
    {zoomOpen && source && <div className="artifact-zoom-dialog" role="dialog" aria-modal="true" aria-label={t('Enlarged result image')}><div><header><strong>{t(artifact.name)}</strong><button onClick={() => setZoomOpen(false)} aria-label={t('Close enlarged image')}>×</button></header><img src={source} alt={t(artifact.name)} /><footer>{index + 1} / {images.length} · {artifact.filename}</footer></div></div>}
  </div>

  return <div className="verified-artifact-viewer">
    <header><span><WorkspaceIcon name="check" size={17} />{t(state === 'verified' ? 'Verified artifact intact' : 'Verifying artifact integrity…')}</span><strong>{t(artifact.name)}</strong></header>
    <div className="artifact-stage">{source ? <img src={source} alt={artifact.name} /> : <div className="artifact-loading">{t('Verifying artifact integrity…')}</div>}</div>
    <dl>
      <div><dt>{t('Result name')}</dt><dd>{artifact.filename}</dd></div>
      <div><dt>{t('Quantity')}</dt><dd>{t(artifact.quantity)}</dd></div>
      <div><dt>{t('Unit')}</dt><dd>{artifact.unit}</dd></div>
      <div><dt>{t('Source run')}</dt><dd>{t('Completed Workbench run')}</dd></div>
      <div><dt>{t('Run ID')}</dt><dd><code>{artifact.message.run_id}</code></dd></div>
      <div><dt>SHA-256</dt><dd><code>{artifact.record.sha256}</code></dd></div>
      <div><dt>{t('Verification state')}</dt><dd>{t(state === 'verified' ? 'Verified artifact intact' : 'Verifying artifact integrity…')}</dd></div>
    </dl>
  </div>
}
