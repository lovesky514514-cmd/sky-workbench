import type { Session } from './types'

// ASK is complete only when the Runtime sends a terminal session for this
// task. A transport close after partial text is an error, never a success.
export async function readMessageStream(
  response: Response,
  expectedSessionId: string,
  onEvent: (event: Record<string, unknown>) => void,
): Promise<Session> {
  if (!response.body) throw new Error('ASK stream has no response body.')
  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  let finalSession: Session | null = null
  try {
    while (true) {
      const { value, done } = await reader.read()
      buffer = (buffer + decoder.decode(value, { stream: !done })).replace(/\r\n/g, '\n')
      if (buffer.length > 1_000_000) throw new Error('ASK event frame exceeded the UI limit.')
      const frames = buffer.split('\n\n')
      buffer = frames.pop() ?? ''
      for (const frame of frames) {
        const data = frame.split('\n').filter((line) => line.startsWith('data:'))
          .map((line) => line.slice(5).trimStart()).join('\n')
        if (!data) continue
        const event = JSON.parse(data) as Record<string, unknown>
        if (!event || typeof event !== 'object') throw new Error('ASK stream returned an invalid event.')
        if (event.type === 'error') throw new Error(String(event.detail ?? 'ASK stream failed.'))
        if (event.type === 'session') {
          const candidate = event.session as Session | undefined
          if (!candidate || candidate.id !== expectedSessionId)
            throw new Error('ASK stream returned a session from a different task.')
          finalSession = candidate
        }
        onEvent(event)
      }
      if (done) break
    }
    if (!finalSession) throw new Error('ASK stream ended before the terminal session arrived.')
    return finalSession
  } finally {
    await reader.cancel().catch(() => undefined)
  }
}
