import type { JavaBridgeStatus } from './types'

export type ModelReadback = {
  tree: Record<string, unknown>
  parameters: Record<string, string>
}

export function hasLiveModel(status: JavaBridgeStatus): boolean {
  return status.bridge_running === true && status.comsol_api_loaded === true &&
    status.real_comsol === true && status.model_connected === true &&
    typeof status.model_tag === 'string' && status.model_tag.length > 0
}

export function sameBridgeAttachment(before: JavaBridgeStatus, after: JavaBridgeStatus): boolean {
  if (!hasLiveModel(before) || !hasLiveModel(after) || before.model_tag !== after.model_tag) return false
  const oldProcess = before.process ?? {}
  const newProcess = after.process ?? {}
  if (typeof oldProcess.pid !== 'number' || !Number.isSafeInteger(oldProcess.pid) || oldProcess.pid <= 0 ||
    typeof oldProcess.started_at !== 'string' || !oldProcess.started_at ||
    typeof oldProcess.model_path !== 'string' || !oldProcess.model_path) return false
  return ['pid', 'started_at', 'model_path'].every((key) => oldProcess[key] === newProcess[key])
}

// Both Bridge responses must certify the same attached model. A connected
// socket alone is not evidence that the tree or its parameters were read back.
export function matchingReadback(
  status: JavaBridgeStatus,
  tree: Record<string, unknown>,
  parameterResponse: Record<string, unknown>,
): ModelReadback | null {
  if (!hasLiveModel(status)) return null
  for (const response of [tree, parameterResponse]) {
    if (response.model_tag !== status.model_tag || response.command_success !== true ||
      response.model_state_verified !== true) return null
  }
  const values = parameterResponse.parameters
  if (!values || typeof values !== 'object' || Array.isArray(values) ||
    !Object.values(values).every((value) => typeof value === 'string')) return null
  return { tree, parameters: values as Record<string, string> }
}
