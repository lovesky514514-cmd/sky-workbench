import assert from 'node:assert/strict'
import test from 'node:test'
import { existsSync } from 'node:fs'
import { registerHooks } from 'node:module'

const sourceRoot = new URL('../src/', import.meta.url).href
registerHooks({
  resolve(specifier, context, next) {
    if (specifier.startsWith('.') && context.parentURL?.startsWith(sourceRoot)) {
      const candidate = new URL(specifier + '.ts', context.parentURL)
      if (existsSync(candidate)) return { url: candidate.href, shortCircuit: true }
    }
    return next(specifier, context)
  },
})
const { decodeResponse, httpFailure, retryAfterSeconds, WorkbenchHttpError } = await import('../src/httpError.ts')

for (const [status, method, kind] of [[429,'GET','cooldown'],[503,'GET','cooldown'],[401,'POST','authentication'],
  [403,'GET','authentication'],[412,'PUT','state_conflict'],[428,'POST','state_conflict'],[422,'POST','format'],
  [501,'POST','unsupported'],[504,'POST','submission_unknown']]) {
  test(`HTTP ${status} ${method} has a safe actionable category`, async () => {
    const headers = new Headers({ 'Retry-After':'3', 'X-SKY-Diagnostic-ID':'a'.repeat(32) })
    const error = httpFailure(status,headers,JSON.stringify({ detail:{ token:'private-token',path:'C:/private' }}),method)
    assert.ok(error instanceof WorkbenchHttpError)
    assert.equal(error.kind,kind)
    assert.equal(error.status,status)
    assert.equal(error.retryAfterSeconds,3)
    assert.equal(error.diagnosticId,'a'.repeat(32))
    assert.doesNotMatch(error.message,/private/)
  })
}

test('204 and HEAD do not parse a missing JSON body', async () => {
  assert.equal(await decodeResponse(new Response(null,{status:204})),undefined)
  assert.equal(await decodeResponse(new Response(null,{status:200}),'HEAD'),undefined)
  await assert.rejects(decodeResponse(new Response('<private-error>',{status:200})), error => error.kind === 'format' && !error.message.includes('private'))
})

test('large and untrusted error bodies are bounded and no URL is fetched', async () => {
  let reads = 0
  const stream = new ReadableStream({ pull(controller) { reads++; controller.enqueue(new Uint8Array(9000)); }, cancel() {} })
  await assert.rejects(decodeResponse(new Response(stream,{status:502}), 'POST'), error => error.kind === 'submission_unknown')
  assert.ok(reads <= 2)
  assert.equal(httpFailure(401,new Headers({'X-SKY-Diagnostic-ID':'C:/private'}),'private').diagnosticId,null)
})

test('Retry-After date and malformed values do not become retry instructions', () => {
  assert.equal(retryAfterSeconds('-1'),null)
  assert.equal(retryAfterSeconds('1.2'),null)
  assert.equal(retryAfterSeconds('Wed, 21 Oct 2015 07:28:00 GMT',Date.parse('2015-10-21T07:27:59Z')),1)
  assert.equal(retryAfterSeconds('9'.repeat(128)),31536000)
})

test('202 preserves the actual pending state', async () => {
  const result = await decodeResponse(new Response(JSON.stringify({state:'PENDING'}),{status:202}))
  assert.equal(result.state,'PENDING')
  await assert.rejects(decodeResponse(new Response(JSON.stringify({success:false,detail:'private'}),{status:200})),
    error => error.kind === 'business' && !error.message.includes('private'))
})
