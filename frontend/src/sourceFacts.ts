// Python source spans count Unicode code points; JS string.slice counts UTF-16 units.
export function factSourceText(source: string, fact: { original: string; span?: [number, number] }, boundSource: string): string | null {
  if (!fact.span || source !== boundSource) return null
  const candidate = Array.from(source).slice(...fact.span).join('')
  return candidate === fact.original ? candidate : null
}
