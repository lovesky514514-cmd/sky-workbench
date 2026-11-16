import { useEffect, useRef, useState, type DragEvent } from 'react'
import { api } from '../api'
import { translate, type Locale } from '../i18n'
import { safeUiError } from '../safeUiError'
import type { ImportedMph } from '../types'

export function ImportedMphWorkspace({ locale, onOpen }: { locale: Locale; onOpen: (item: ImportedMph) => void }) {
  const t = (text: string) => translate(locale, text)
  const input = useRef<HTMLInputElement>(null)
  const generation = useRef(0)
  const [items, setItems] = useState<ImportedMph[]>([])
  const [selected, setSelected] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [dragging, setDragging] = useState(false)
  const [error, setError] = useState('')
  const current = items.find((item) => item.id === selected)

  useEffect(() => {
    let live = true
    void api.importedMph().then((value) => { if (live) setItems(value.items) }).catch((cause) => { if (live) setError(safeUiError(cause)) })
    return () => { live = false; generation.current += 1 }
  }, [])

  async function importFile(file?: File) {
    if (!file) return
    if (!file.name.toLowerCase().endsWith('.mph') || !file.size || file.size > 1024 ** 3) {
      setError(t('Choose a non-empty MPH file under 1 GiB.')); return
    }
    const token = ++generation.current
    setBusy(true); setError('')
    try {
      const item = await api.importMph(file)
      if (generation.current !== token) return
      setItems((before) => [item, ...before]); setSelected(item.id)
      onOpen(item)
    } catch (cause) { if (generation.current === token) setError(safeUiError(cause)) }
    finally { if (generation.current === token) setBusy(false) }
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
        <div className="warm-import-list"><h2>{t('Imported files')}</h2>{items.length === 0 ? <p>{t('No imported MPH files yet.')}</p> : items.map((item) => <button key={item.id} disabled={busy} className={selected === item.id ? 'is-current' : ''} onClick={() => { setSelected(item.id); setError('') }}><strong>{item.original_filename}</strong><small>{(item.size_bytes / 1024 / 1024).toFixed(1)} MiB · {item.imported_at.slice(0, 10)}</small></button>)}</div>
      </section>
      <section className="warm-import-preview" aria-live="polite"><h2>{t('Model preview')}</h2>{!current ? <p>{t('Select an imported MPH file to inspect it.')}</p> : <><strong>{current.original_filename}</strong><small>{t('Stored in')}: {current.storage_relative_path}</small><small>SHA-256: {current.sha256}</small><div className="warm-import-preview-actions"><button disabled={busy} onClick={() => onOpen(current)}>{t('Load with COMSOL')}</button><button onClick={() => void api.openImportedModels()}>{t('Open imported folder')}</button></div></>}</section>
    </div>
  </div>
}
