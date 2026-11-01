import { useState } from 'react'
import { api } from '../api'
import { safeUiError } from '../safeUiError'

export interface KnowledgeHit {
  text: string
  citation: { title: string; file: string; page?: number | null; chunk_id: string }
  metadata?: Record<string, unknown>
  evidence?: Record<string, unknown>
}
export interface KnowledgeReply { results: KnowledgeHit[]; evidence_status: 'EVIDENCE_FOUND' | 'NO_EVIDENCE' }

export function KnowledgeSearchPanel({ zh }: { zh: boolean }) {
  const [engine, setEngine] = useState('')
  const [version, setVersion] = useState('')
  const [query, setQuery] = useState('')
  const [reply, setReply] = useState<KnowledgeReply | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [title, setTitle] = useState('')
  const [source, setSource] = useState('')
  const [content, setContent] = useState('')
  const [imported, setImported] = useState(false)
  const search = async () => {
    setBusy(true); setError('')
    try { setReply(await api.searchKnowledge(query, { ...(engine ? { engine } : {}), ...(version ? { engine_version: version } : {}) })) }
    catch (cause) { setError(safeUiError(cause)) }
    finally { setBusy(false) }
  }
  const importNote = async () => {
    if (!engine || !title.trim() || !content.trim()) return
    if (new TextEncoder().encode(content).length > 64000) {
      setError(zh ? '单条资料正文最多 64 KB，请分段导入。' : 'A note may contain at most 64 KB. Import it in sections.')
      return
    }
    setBusy(true); setError(''); setImported(false)
    try {
      await api.importKnowledgeUnits([{ unit_id: crypto.randomUUID(), title, content,
        metadata: { engine, module: 'project_reference', source_url: source || 'unknown:project-note',
          applies_to_versions: version ? [version] : [] } }])
      setImported(true)
    } catch (cause) { setError(safeUiError(cause)) }
    finally { setBusy(false) }
  }
  return <section className="knowledge-search-panel" aria-label={zh ? '科学资料检索' : 'Scientific source search'}>
    <label>{zh ? '仿真引擎' : 'Simulation engine'}<select disabled={busy} value={engine} onChange={(event) => { setEngine(event.target.value); setReply(null); setImported(false) }}>
      <option value="">{zh ? '全部引擎' : 'All engines'}</option>
      {['COMSOL', 'MATLAB', 'VASP', 'Materials Studio', 'LAMMPS', 'Mathics3'].map((name) => <option key={name}>{name}</option>)}
    </select></label>
    <label>{zh ? '适用版本（可选）' : 'Applicable version (optional)'}<input disabled={busy} value={version} onChange={(event) => { setVersion(event.target.value); setReply(null) }} /></label>
    <label>{zh ? '问题或命令' : 'Question or command'}<input disabled={busy} value={query} onChange={(event) => setQuery(event.target.value)} maxLength={2000} /></label>
    <button disabled={busy || !query.trim()} onClick={() => void search()}>{zh ? '检索来源' : 'Find sources'}</button>
    {reply?.evidence_status === 'NO_EVIDENCE' && <p role="status">{zh
      ? '未找到符合当前引擎、版本和项目范围的证据。可补充资料，或在建模草案中选择待确认的参数建议。'
      : 'No evidence matches this engine, version and project. Add sources or review proposed assumptions in the model draft.'}</p>}
    {reply?.results.map((hit) => <article key={hit.citation.chunk_id}>
      <strong>{hit.citation.title}</strong><p>{hit.text}</p>
      {/^https?:\/\//i.test(hit.citation.file) ? <a href={hit.citation.file} target="_blank" rel="noreferrer">{zh ? '查看来源' : 'Open source'}</a> : <span>{hit.citation.file}</span>}
      <small>{hit.citation.page == null ? (zh ? '页码未知' : 'Page unknown') : `p. ${hit.citation.page}`} · {zh ? '科学支持尚未验证' : 'Scientific support unverified'}</small>
    </article>)}
    <details><summary>{zh ? '添加当前项目的资料条目' : 'Add a source note to this project'}</summary>
      <p>{zh ? '条目保存在当前项目，并标记为未验证；不会因此进入公开随包知识库。' : 'Notes remain in this project and are marked unverified. They do not become bundled public knowledge.'}</p>
      <label>{zh ? '标题' : 'Title'}<input disabled={busy} value={title} onChange={(event) => setTitle(event.target.value)} maxLength={300} /></label>
      <label>{zh ? '来源网址或本地位置' : 'Source URL or local location'}<input disabled={busy} value={source} onChange={(event) => setSource(event.target.value)} maxLength={2000} /></label>
      <label>{zh ? '资料正文' : 'Source text'}<textarea disabled={busy} value={content} onChange={(event) => setContent(event.target.value)} maxLength={64000} rows={5} /></label>
      <button disabled={busy || !engine || !title.trim() || !content.trim()} onClick={() => void importNote()}>{zh ? '保存项目条目' : 'Save project note'}</button>
      {imported && <p role="status">{zh ? '已保存到当前项目；证据状态仍为未验证。' : 'Saved in this project; evidence remains unverified.'}</p>}
    </details>
    {error && <p role="alert">{error}</p>}
  </section>
}
