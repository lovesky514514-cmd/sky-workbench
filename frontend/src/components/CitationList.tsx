import type { Citation } from '../types'
import { currentLocale, translate, type Locale } from '../i18n'

export function CitationList({ citations, locale }: { citations: Citation[]; locale?: Locale }) {
  const t = (source: string) => translate(locale ?? currentLocale(), source)
  if (!citations.length) return <p className="muted-block">{t('No traceable sources attached.')}</p>
  return <div className="citation-list">{citations.map((citation) => (
    <details key={`${citation.document_id}-${citation.chunk_id}`}>
      <summary><span>{citation.title}</span><em>{citation.page ? `p. ${citation.page}` : citation.page_status === 'page_unknown' ? 'page_unknown' : citation.source_type}</em></summary>
      <dl>
        <dt>{t('File')}</dt><dd title={citation.file}>{citation.file_name || citation.file}</dd>
        <dt>{t('Section')}</dt><dd>{citation.section || t('Not detected')}</dd>
        <dt>{t('Unit')}</dt><dd><code>{citation.unit_id || citation.chunk_id}</code></dd>
        <dt>{t('Page status')}</dt><dd>{citation.page_status || (citation.page ? 'precise' : 'page_unknown')}</dd>
        {citation.doi && <><dt>DOI</dt><dd>{citation.doi}</dd></>}
        {citation.year && <><dt>{t('Year')}</dt><dd>{citation.year}</dd></>}
      </dl>
      {citation.excerpt && <blockquote>{citation.excerpt}</blockquote>}
    </details>
  ))}</div>
}
