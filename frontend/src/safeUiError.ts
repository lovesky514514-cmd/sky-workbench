// A technical failure may contain a user path, stack, or credential. Keep raw
// diagnostics in local logs; the ordinary UI shows only bounded safe wording.
const privateDetail = /(?:[A-Za-z]:[\\/]|\\\\[^\\\s]+\\|\/(?:Users|home|mnt|tmp)\/|Traceback \(most recent call last\)|-----BEGIN [A-Z ]+PRIVATE KEY-----|(?:api[_-]?key|bearer|token|secret)\s*[:=]|\bsk-[A-Za-z0-9_-]{12,}\b|\bBearer\s+[A-Za-z0-9._~-]{8,}\b|\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b)/i

export function safeUiError(value: unknown): string {
  const raw = String(value ?? '').trim()
  if (!raw || privateDetail.test(raw) || raw.length > 500) {
    return 'Operation did not complete. Open diagnostic logs for technical details.'
  }
  return raw
}

// RC4.2 SafeError has user-facing fields; arbitrary backend `detail` objects
// must not be stringified into a visible raw JSON/exception blob.
export function responseError(body: string, status: number): string {
  try {
    const parsed = JSON.parse(body) as { detail?: unknown }
    const detail = parsed.detail
    if (detail && typeof detail === 'object' && !Array.isArray(detail)) {
      const safe = detail as { title?: unknown; summary?: unknown }
      if (typeof safe.title === 'string' && typeof safe.summary === 'string') {
        return safeUiError(`${safe.title}: ${safe.summary}`)
      }
    }
  } catch { /* Non-JSON responses are not trusted for ordinary display. */ }
  return `Operation did not complete (HTTP ${status}). Open diagnostic logs.`
}
