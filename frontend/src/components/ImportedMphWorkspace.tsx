import { useEffect, useRef, useState, type DragEvent } from 'react'
import { api } from '../api'
import { translate, type Locale } from '../i18n'
import { safeUiError } from '../safeUiError'
import type { ImportedMph, ImportedMphPreview } from '../types'

const delay = (ms: number) => new Promise((resolve) => window.setTimeout(resolve, ms))
const tags = ['components', 'geometry', 'physics', 'mesh', 'studies', 'solvers', 'results'] as const

export function ImportedMphWorkspace({ locale }: { locale: Locale }) {
  const t = (text: string) => translate(locale, text)
  const input = useRef<HTMLInputElement>(null)
  const generation = useRef(0)
  const [items, setItems] = useState<ImportedMph[]>([])
  const [selected, setSelected] = useState<string | null>(null)
  const [preview, setPreview] = useState<ImportedMphPreview | null>(null)
  const [busy, setBusy] = useState(false)
  const [dragging, setDragging] = useState(false)
  const [error, setError] = useState('')
  const current = items.find((item) => item.id === selected)

  useEffect(() => {
    let live = true
    void api.importedMph().then((value) => { if (live) setItems(value.items) }).catch((cause) => { if (live) setError(safeUiError(cause)) })
    return () => { live = false; generation.current += 1 }
  }, [])

  // Every preview poll is bound to its selected import. Switching items or
  // leaving the page cannot certify a late readback against the wrong MPH.
  async function loadPreview(id: string) {
    const token = ++generation.current
    setSelected(id); setPreview(null); setError(''); setBusy(true)
    try {
      await api.startImportedMphPreview(id)
      for (let attempt = 0; attempt < 45; attempt += 1) {
        if (generation.current !== token) return
        const next = await api.importedMphPreview(id)
        if (next.preview_state !== 'starting') { setPreview(next); return }
        await delay(2000)
      }
      if (generation.current === token) setPreview({ preview_state: 'unavailable', model_state_verified: false, scientific_validation_passed: false, reason: 'preview_timeout' })
    } catch (cause) {
      if (generation.current === token) setError(safeUiError(cause))
    } finally {
      if (generation.current === token) setBusy(false)
    }
  }

  async function importFile(file?: File) {
    if (!file) return
    if (!file.name.toLowerCase().endsWith('.mph') || !file.size || file.size > 1024 ** 3) {
      setError(t('Choose a non-empty MPH file under 1 GiB.')); return
    }
    setBusy(true); setError(''); setPreview(null)
    try {
      const item = await api.importMph(file)
      setItems((before) => [item, ...before]); setSelected(item.id)
      await loadPreview(item.id)
    } catch (cause) { setError(safeUiError(cause)); setBusy(false) }
  }

  function onDrop(event: DragEvent<HTMLElement>) {
    event.preventDefault(); setDragging(false)
    if (event.dataTransfer.files.length !== 1) { setError(t('Drop one MPH file at a time.')); return }
    void importFile(event.dataTransfer.files[0])
  }

  return <div className="warm-import-workspace">
    <div className="warm-page-heading"><div><h1>{t('Imported MPH models')}</h1><p>{t('Original files are preserved. COMSOL opens a separate working copy for preview and later edits.')}</p></div><span className="warm-status-pill">{items.length} MPH</span></div>
    <div className="warm-import-layout">
      <section className={`warm-import-drop ${dragging ? 'is-dragging' : ''}`} onDragOver={(event) => { event.preventDefault(); setDragging(true) }} onDragLeave={() => setDragging(false)} onDrop={onDrop}>
        <input ref={input} type="file" accept=".mph" hidden onChange={(event) => { void importFile(event.target.files?.[0]); event.target.value = '' }} />
        <strong>{t('Drop an MPH model here')}</strong><p>{t('or choose a local file. Import does not run a solver or certify scientific results.')}</p>
        <button disabled={busy} onClick={() => input.current?.click()}>{busy ? t('Loading model…') : t('Choose MPH file')}</button>
        {error && <p className="warm-import-error" role="alert">{error}</p>}
        <div className="warm-import-list"><h2>{t('Imported files')}</h2>{items.length === 0 ? <p>{t('No imported MPH files yet.')}</p> : items.map((item) => <button key={item.id} className={selected === item.id ? 'is-current' : ''} onClick={() => { generation.current += 1; setSelected(item.id); setPreview(null); setError('') }}><strong>{item.original_filename}</strong><small>{(item.size_bytes / 1024 / 1024).toFixed(1)} MiB · {item.imported_at.slice(0, 10)}</small></button>)}</div>
      </section>
      <section className="warm-import-preview" aria-live="polite"><h2>{t('Model preview')}</h2>{!current ? <p>{t('Select an imported MPH file to inspect it.')}</p> : <><strong>{current.original_filename}</strong><small>{t('Stored in')}: {current.storage_relative_path}</small><small>SHA-256: {current.sha256}</small><div className="warm-import-preview-actions"><button disabled={busy} onClick={() => void loadPreview(current.id)}>{t('Load with COMSOL')}</button><button onClick={() => void api.openImportedModels()}>{t('Open imported folder')}</button></div>{busy && <p>{t('Waiting for real COMSOL model readback…')}</p>}{preview?.preview_state === 'verified' && preview.model_state_verified && <><p className="warm-import-verified">{t('Live COMSOL model tree verified')} · {preview.comsol_version}</p><dl>{tags.map((key) => <div key={key}><dt>{t(key)}</dt><dd>{Array.isArray(preview.model_tree?.[key]) ? (preview.model_tree[key] as string[]).join(', ') || '—' : '—'}</dd></div>)}</dl></>}{preview?.preview_state === 'unavailable' && <p className="warm-import-error">{t('Preview unavailable')} · {preview.reason}</p>}<p className="warm-import-note">{t('A model-tree preview is not a solve, a modification, or scientific validation.')}</p></>}</section>
    </div>
  </div>
}
