import assert from 'node:assert/strict'
import test from 'node:test'

import { readMessageStream } from '../src/messageStream.ts'

const encoder = new TextEncoder()
const response = (...chunks) => new Response(new ReadableStream({
  start(controller) {
    for (const chunk of chunks) controller.enqueue(encoder.encode(chunk))
    controller.close()
  },
}))

test('ASK stream accepts a chunked terminal session for the requested task', async () => {
  const events = []
  const result = await readMessageStream(response(
    'data: {"type":"delta","text":"hel',
    'lo"}\r\n\r\n',
    'data: {"type":"session","session":{"id":"task-a","state":"IDLE"}}\n\n',
  ), 'task-a', (event) => events.push(event))
  assert.equal(result.id, 'task-a')
  assert.deepEqual(events.map((event) => event.type), ['delta', 'session'])
})

test('ASK stream close without terminal session is a failure, not an old-session fallback', async () => {
  await assert.rejects(readMessageStream(response('data: {"type":"delta","text":"partial"}\n\n'),
    'task-a', () => {}), /ended before.*session/i)
})

test('ASK stream rejects a terminal session from another task', async () => {
  await assert.rejects(readMessageStream(response(
    'data: {"type":"session","session":{"id":"task-b"}}\n\n',
  ), 'task-a', () => {}), /different task/i)
})

test('ASK stream reports backend errors and malformed frames without claiming completion', async () => {
  await assert.rejects(readMessageStream(response('data: {"type":"error","detail":"provider failed"}\n\n'),
    'task-a', () => {}), /provider failed/)
  await assert.rejects(readMessageStream(response('data: {bad}\n\n'), 'task-a', () => {}), SyntaxError)
})
