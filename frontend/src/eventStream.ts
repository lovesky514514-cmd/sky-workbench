export type EventStreamOptions = {
  reconnect?: boolean
  reconnectDelayMs?: number
  onConnected?: () => void
}

// Long-lived session and Bridge feeds resume from the last acknowledged ID.
// Bounded feeds such as indexing leave reconnect off and finish normally.
export function openEventStream(
  url: string,
  sessionToken: string,
  onMessage: (payload: Record<string, unknown>) => void,
  onError: (cause: unknown) => void,
  options: EventStreamOptions = {},
): AbortController {
  const controller = new AbortController()
  void (async () => {
    let lastEventId = ''
    while (!controller.signal.aborted) {
      try {
        const headers: Record<string, string> = {
          Accept: 'text/event-stream',
          ...(sessionToken ? { 'X-Workbench-Token': sessionToken } : {}),
          ...(lastEventId ? { 'Last-Event-ID': lastEventId } : {}),
        }
        const response = await fetch(url, { headers, signal: controller.signal })
        if (!response.ok || !response.body) {
          onError(new Error(`Event stream unavailable (HTTP ${response.status}).`))
          if ([401, 403, 404].includes(response.status) || !options.reconnect) return
        } else {
          options.onConnected?.()
          const reader = response.body.getReader()
          const decoder = new TextDecoder()
          let buffer = ''
          try {
            while (!controller.signal.aborted) {
              const { value, done } = await reader.read()
              buffer = (buffer + decoder.decode(value, { stream: !done })).replace(/\r\n/g, '\n')
              if (buffer.length > 1_000_000) throw new Error('Live event frame exceeded the UI limit.')
              const frames = buffer.split('\n\n')
              buffer = frames.pop() ?? ''
              for (const frame of frames) {
                if (controller.signal.aborted) break
                const lines = frame.split('\n')
                const id = lines.find((line) => line.startsWith('id:'))?.slice(3).trim()
                const data = lines.filter((line) => line.startsWith('data:'))
                  .map((line) => line.slice(5).trimStart()).join('\n')
                if (data) {
                  const payload: unknown = JSON.parse(data)
                  if (!payload || typeof payload !== 'object' || Array.isArray(payload))
                    throw new SyntaxError('Live event must be an object.')
                  try { onMessage(payload as Record<string, unknown>) }
                  catch (cause) {
                    // Replaying a frame cannot fix a deterministic UI handler
                    // failure; stop instead of looping through the same event.
                    onError(cause)
                    return
                  }
                  // A malformed frame must never advance the replay cursor.
                  if (id && /^\d+$/.test(id)) lastEventId = id
                }
              }
              if (done) break
            }
          } finally { await reader.cancel().catch(() => undefined) }
          if (!options.reconnect || controller.signal.aborted) return
          onError(new Error('Live event stream closed.'))
        }
      } catch (cause) {
        if (controller.signal.aborted) return
        onError(cause)
        // Retrying the same malformed frame cannot repair its content.
        if (cause instanceof SyntaxError) return
        if (!options.reconnect) return
      }
      await waitForStreamRetry(controller.signal, options.reconnectDelayMs ?? 1000)
    }
  })()
  return controller
}

function waitForStreamRetry(signal: AbortSignal, delayMs: number): Promise<void> {
  return new Promise((resolve) => {
    const finish = () => { signal.removeEventListener('abort', onAbort); resolve() }
    const onAbort = () => { clearTimeout(timer); finish() }
    const timer = setTimeout(finish, Math.max(0, delayMs))
    signal.addEventListener('abort', onAbort, { once: true })
    if (signal.aborted) onAbort()
  })
}
