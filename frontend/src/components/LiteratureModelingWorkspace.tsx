import { useState } from 'react'
import { api } from '../api'
import { translate, type Locale } from '../i18n'
import { safeUiError } from '../safeUiError'
import type { LiteratureModelCandidate } from '../types'

export function LiteratureModelingWorkspace({ locale, onChoose }: { locale: Locale; onChoose: (excerpt: string) => void }) {
  const t = (text: string) => translate(locale, text)
  const [title, setTitle] = useState('')
  const [text, setText] = useState('')
  const [sourceUrl, setSourceUrl] = useState('')
  const [file, setFile] = useState<File | null>(null)
  const [candidate, setCandidate] = useState<LiteratureModelCandidate | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  // Extraction is a read-only, local proposal. Only the explicit Yes button
  // copies the exact source excerpt into a new task; PLAN and RUN remain gated.
  async function extract() {
    setBusy(true); setError(''); setCandidate(null)
    try {
      if (file) {
        if (!file.name.toLowerCase().endsWith('.pdf') || file.size > 15 * 1024 * 1024 || !file.size) throw new Error(t('Choose a PDF under 15 MiB.'))
        setCandidate(await api.extractLiteraturePdf(file))
      } else {
        setCandidate(await api.extractLiteratureText(title.trim(), text.trim()))
      }
    } catch (cause) { setError(safeUiError(cause)) }
    finally { setBusy(false) }
  }

  // A typed article URL is resolved by the backend to a fixed official API.
  // This never crawls an arbitrary site or downloads its PDF implicitly.
  async function extractSource() {
    setBusy(true); setError(''); setCandidate(null)
    try { setCandidate(await api.extractLiteratureSource(sourceUrl.trim())) }
    catch (cause) { setError(safeUiError(cause)) }
    finally { setBusy(false) }
  }

  return <div className="warm-literature-workspace">
    <div className="warm-page-heading"><div><h1>{t('Literature-to-model proposal')}</h1><p>{t('Extract a source-bound candidate. Decide whether to open a plan; importing a paper never builds a model.')}</p></div><span className="warm-status-pill">{t('Optional extension')}</span></div>
    <div className="warm-literature-layout"><section className="warm-literature-input"><h2>{t('Source literature')}</h2><label>{t('arXiv or PMC article URL')}<input type="url" value={sourceUrl} onChange={(event) => setSourceUrl(event.target.value)} maxLength={500} placeholder="https://arxiv.org/abs/…" /></label><p>{t('Only official arXiv/PMC article abstracts from the last three years are read. No automatic PDF download or broad crawling.')}</p><button disabled={busy || !sourceUrl.trim()} onClick={() => void extractSource()}>{busy ? t('Extracting…') : t('Read recent article')}</button><div className="warm-literature-divider" /><label>{t('Paper title')}<input value={title} onChange={(event) => setTitle(event.target.value)} maxLength={240} placeholder={t('Title for a pasted excerpt')} /></label><label>{t('Paste model methods or parameters')}<textarea value={text} onChange={(event) => setText(event.target.value)} maxLength={50_000} placeholder={t('Include physics, dimensions, material values, boundary conditions and study type.')} /></label><label>{t('Or choose a PDF')}<input type="file" accept=".pdf,application/pdf" onChange={(event) => setFile(event.target.files?.[0] ?? null)} /></label>{file && <small>{t('Selected PDF')}: {file.name} · {(file.size / 1024 / 1024).toFixed(1)} MiB</small>}<p>{t('PDF text is read locally and is not added to the Knowledge Base or retained by this action. Scanned PDFs without selectable text need manual excerpts.')}</p><button className="warm-primary" disabled={busy || (!file && (!title.trim() || !text.trim()))} onClick={() => void extract()}>{busy ? t('Extracting…') : t('Extract model candidate')}</button>{error && <p className="warm-import-error" role="alert">{error}</p>}</section>
      <section className="warm-literature-review"><h2>{t('Human review')}</h2>{!candidate ? <p>{t('No candidate extracted yet. Nothing has been sent to COMSOL.')}</p> : <><span className={candidate.can_open_plan ? 'warm-import-verified' : 'warm-import-error'}>{t(candidate.status)}</span><p><strong>{candidate.source_title}</strong>{candidate.page_number ? ` · ${t('Page')} ${candidate.page_number}` : ''}{candidate.publication_date ? ` · ${candidate.publication_date}` : ''}</p>{candidate.source_url && <p><a href={candidate.source_url} target="_blank" rel="noopener noreferrer">{candidate.source_url}</a></p>}<blockquote>{candidate.excerpt || t('No selectable model evidence found.')}</blockquote><dl><div><dt>{t('Physics')}</dt><dd>{candidate.intent.physics.join(', ') || '—'}</dd></div><div><dt>{t('Geometry')}</dt><dd>{candidate.intent.geometry.join(', ') || '—'}</dd></div><div><dt>{t('Missing information')}</dt><dd>{candidate.intent.unknowns.concat(candidate.intent.validation_issues).join('; ') || '—'}</dd></div></dl><p className="warm-literature-warning">{t('This is an unverified extraction, not a validated model or scientific result. Review the paper and every parameter before planning.')}</p><h3>{t('Do you want to model this candidate?')}</h3><div className="warm-literature-actions"><button className="warm-primary" disabled={!candidate.can_open_plan} onClick={() => onChoose(candidate.excerpt)}>{t('Yes · open a new plan draft')}</button><button onClick={() => setCandidate(null)}>{t('No · do not model')}</button></div>{candidate.warning && <small>{t(candidate.warning)}</small>}{candidate.extraction_method && <small> · {t('DeepSeek selected an exact source window; all scientific claims remain unverified.')}</small>}</>}</section></div>
  </div>
}
