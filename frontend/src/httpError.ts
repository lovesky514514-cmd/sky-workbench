import { responseError } from './safeUiError'

export type HttpFailureKind = 'cooldown' | 'authentication' | 'state_conflict' | 'format' | 'unsupported' | 'submission_unknown' | 'service' | 'business'

export class WorkbenchHttpError extends Error {
  status: number
  kind: HttpFailureKind
  retryAfterSeconds: number | null
  diagnosticId: string | null

  constructor(message: string, status: number, kind: HttpFailureKind, retryAfterSeconds: number | null, diagnosticId: string | null) {
    super(message)
    this.name = 'WorkbenchHttpError'
    this.status = status
    this.kind = kind
    this.retryAfterSeconds = retryAfterSeconds
    this.diagnosticId = diagnosticId
  }
}

export function retryAfterSeconds(value: string | null, now = Date.now()): number | null {
  if (!value || value.length > 128) return null
  const text = value.trim()
  if (/^\d+$/.test(text)) return Math.min(31536000, Number(text))
  if (!/GMT$/.test(text)) return null
  const date = Date.parse(text)
  return Number.isFinite(date) ? Math.min(31536000, Math.max(0, (date - now) / 1000)) : null
}

export function httpFailure(status: number, headers: Headers, body: string, method = 'GET'): WorkbenchHttpError {
  const kind: HttpFailureKind = [401, 403, 407].includes(status) ? 'authentication'
    : [409, 412, 428].includes(status) ? 'state_conflict'
    : [400, 406, 411, 413, 414, 415, 417, 422, 431].includes(status) ? 'format'
    : [501, 505, 510].includes(status) ? 'unsupported'
    : !['GET', 'HEAD'].includes(method.toUpperCase()) && [408, 500, 502, 503, 504].includes(status) ? 'submission_unknown'
    : [429, 503].includes(status) ? 'cooldown' : 'service'
  const descriptions: Record<HttpFailureKind, string> = {
    cooldown: '服务正在冷却，请等待后核查状态。',
    authentication: '需要重新登录或核对访问权限。',
    state_conflict: '当前状态或版本已变化，请刷新后复核。',
    format: '请求或数据格式不符合接口要求。',
    unsupported: '服务不支持当前操作。',
    submission_unknown: '任务可能已提交，需核查当前状态，请勿重复 RUN。',
    service: '服务请求未完成，请查看诊断记录。',
    business: '服务返回业务失败，请核查当前任务。',
  }
  const id = headers.get('X-SKY-Diagnostic-ID')
  return new WorkbenchHttpError(`${descriptions[kind]} ${responseError(body, status)}`, status, kind,
    retryAfterSeconds(headers.get('Retry-After')), id && /^[a-f0-9]{32}$/.test(id) ? id : null)
}

export async function decodeResponse<T>(response: Response, method = 'GET'): Promise<T> {
  if (!response.ok) {
    // 错误正文有界读取，不将代理 HTML、密钥或异常栈当作可展示内容。
    const reader = response.body?.getReader()
    let size = 0
    const chunks: Uint8Array[] = []
    try {
      while (reader) {
        const { done, value } = await reader.read()
        if (done) break
        size += value.byteLength
        if (size > 8192) break
        chunks.push(value)
      }
    } finally { await reader?.cancel().catch(() => undefined) }
    const decoder = new TextDecoder()
    const body = size > 8192 ? '' : chunks.map(value => decoder.decode(value, { stream: true })).join('') + decoder.decode()
    throw httpFailure(response.status, response.headers, body, method)
  }
  if (response.status === 204 || response.status === 205 || method.toUpperCase() === 'HEAD') return undefined as T
  let value: T
  try { value = await response.json() as T }
  catch { throw new WorkbenchHttpError('服务返回的数据格式不符合接口要求。', response.status, 'format', null, null) }
  if (value && typeof value === 'object' && 'success' in value && value.success === false) {
    throw new WorkbenchHttpError('服务返回业务失败，请核查当前任务。', response.status, 'business', null, null)
  }
  return value
}
