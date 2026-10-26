import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

import { openEventStream } from '../src/eventStream.ts'

const encoder = new TextEncoder()
const frame = (id) => new Response(new ReadableStream({
  start(controller) {
    controller.enqueue(encoder.encode(`id: ${id}\ndata: {"sequence":${id}}\n\n`))
    controller.close()
  },
}), { status: 200, headers: { 'Content-Type': 'text/event-stream' } })

test('a live session stream resumes after a transport close with its last event ID', async () => {
  const originalFetch = globalThis.fetch
  const requests = []
  globalThis.fetch = async (url, init) => {
    requests.push({ url, headers: init.headers })
    return frame(requests.length === 1 ? 41 : 42)
  }
  let stream
  try {
    const received = []
    let connections = 0
    const finished = new Promise((resolve) => {
      stream = openEventStream('/api/sessions/test/events?after=40', 'isolated-test-token', (event) => {
        received.push(event.sequence)
        if (event.sequence === 42) { stream.abort(); resolve() }
      }, () => {}, { reconnect: true, reconnectDelayMs: 10, onConnected: () => { connections += 1 } })
    })
    await Promise.race([finished, new Promise((_, reject) => setTimeout(() => reject(new Error('stream did not resume')), 1000))])
    assert.deepEqual(received, [41, 42])
    assert.equal(requests.length, 2)
    assert.equal(connections, 2)
    assert.equal(requests[1].headers['Last-Event-ID'], '41')
    assert.equal(requests[1].headers['X-Workbench-Token'], 'isolated-test-token')
  } finally {
    stream?.abort()
    globalThis.fetch = originalFetch
  }
})

test('session reconnect reconciles the current journal before showing certification', () => {
  const feed = readFileSync(new URL('../src/useSessionEventFeed.ts', import.meta.url), 'utf8')
  assert.match(feed, /onConnected: \(\) => \{[\s\S]*?reconcileCurrentEvidence\([\s\S]*?refreshSession\(session\.id, ticket\)/)
})

test('a bounded knowledge stream ends without reconnecting', async () => {
  const originalFetch = globalThis.fetch
  let calls = 0
  globalThis.fetch = async () => { calls += 1; return frame(7) }
  try {
    const stream = openEventStream('/api/knowledge/events?after=0', '', () => {}, () => {})
    await new Promise((resolve) => setTimeout(resolve, 60))
    stream.abort()
    assert.equal(calls, 1)
  } finally { globalThis.fetch = originalFetch }
})

test('authentication rejection is reported once and does not retry', async () => {
  const originalFetch = globalThis.fetch
  let calls = 0
  const errors = []
  globalThis.fetch = async () => {
    calls += 1
    return new Response('{"detail":"denied"}', { status: 401 })
  }
  try {
    const stream = openEventStream('/api/sessions/test/events', '', () => {},
      (error) => errors.push(String(error)), { reconnect: true, reconnectDelayMs: 10 })
    await new Promise((resolve) => setTimeout(resolve, 60))
    stream.abort()
    assert.equal(calls, 1)
    assert.equal(errors.length, 1)
    assert.match(errors[0], /HTTP 401/)
  } finally { globalThis.fetch = originalFetch }
})

test('a malformed event does not advance the cursor or loop forever', async () => {
  const originalFetch = globalThis.fetch
  let calls = 0
  const errors = []
  globalThis.fetch = async () => {
    calls += 1
    return new Response(`id: 17\ndata: {broken}\n\n`, { status: 200 })
  }
  try {
    const stream = openEventStream('/api/sessions/test/events', '', () => {},
      (error) => errors.push(error), { reconnect: true, reconnectDelayMs: 10 })
    await new Promise((resolve) => setTimeout(resolve, 60))
    stream.abort()
    assert.equal(calls, 1)
    assert.equal(errors.length, 1)
    assert.ok(errors[0] instanceof SyntaxError)
  } finally { globalThis.fetch = originalFetch }
})

test('non-object events and UI handler failures stop replay instead of retrying forever', async () => {
  const originalFetch = globalThis.fetch
  try {
    for (const [payload, callback] of [['null', () => {}],
      ['{"type":"progress"}', () => { throw new Error('handler failed') }]]) {
      let calls = 0
      const errors = []
      globalThis.fetch = async () => { calls += 1; return new Response(`id: 8\ndata: ${payload}\n\n`) }
      const stream = openEventStream('/api/sessions/test/events', '', callback,
        (error) => errors.push(error), { reconnect: true, reconnectDelayMs: 10 })
      await new Promise((resolve) => setTimeout(resolve, 60))
      stream.abort()
      assert.equal(calls, 1)
      assert.equal(errors.length, 1)
    }
  } finally { globalThis.fetch = originalFetch }
})

test('aborting during reconnect wait prevents a new request', async () => {
  const originalFetch = globalThis.fetch
  let calls = 0
  globalThis.fetch = async () => { calls += 1; return frame(1) }
  try {
    const stream = openEventStream('/api/sessions/test/events', '', () => {},
      () => {}, { reconnect: true, reconnectDelayMs: 100 })
    await new Promise((resolve) => setTimeout(resolve, 20))
    stream.abort()
    await new Promise((resolve) => setTimeout(resolve, 130))
    assert.equal(calls, 1)
  } finally { globalThis.fetch = originalFetch }
})
