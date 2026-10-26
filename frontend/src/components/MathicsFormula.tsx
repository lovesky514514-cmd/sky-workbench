import { createElement, useMemo, type ReactNode } from 'react'

const mathTags = new Set([
  'math', 'mrow', 'mi', 'mn', 'mo', 'mfrac', 'msup', 'msub', 'msubsup',
  'msqrt', 'mroot', 'mtext', 'mstyle', 'mfenced', 'mtable', 'mtr', 'mtd',
  'mover', 'munder', 'munderover', 'menclose',
])

function safeMathMl(xml: string): ReactNode {
  if (!xml || xml.length > 65_536 || /<!DOCTYPE|<!ENTITY/i.test(xml) || typeof DOMParser === 'undefined') return null
  const document = new DOMParser().parseFromString(xml, 'application/xml')
  if (document.documentElement.localName !== 'math' || document.querySelector('parsererror')) return null
  let count = 0
  let valid = true

  // Recreate only MathML elements and text. Engine output never becomes raw HTML,
  // and attributes such as links, event handlers and external image URLs are dropped.
  function visit(node: Node): ReactNode {
    if (++count > 256) { valid = false; return null }
    if (node.nodeType === 3) return node.textContent
    if (node.nodeType !== 1) return null
    const element = node as Element
    if (!mathTags.has(element.localName)) { valid = false; return null }
    const children = Array.from(element.childNodes, visit)
    return createElement(element.localName, element.localName === 'math' ? { display: 'block' } : null, ...children)
  }
  const formula = visit(document.documentElement)
  return valid ? formula : null
}

export function MathicsFormula({ mathml, latex }: { mathml: string; latex: string }) {
  const formula = useMemo(() => safeMathMl(mathml), [mathml])
  return formula
    ? <div className="warm-script-formula" aria-label="Mathics formula">{formula}</div>
    : latex ? <code>{latex}</code> : null
}
