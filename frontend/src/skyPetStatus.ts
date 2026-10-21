// Static, UI-only status from current Workbench state. No synthetic progress.
export type SkyPetInput = {
  warning: string
  busy: boolean
  askStreaming: boolean
  pendingIntent: boolean
  sessionState?: string
  scientificVerified: boolean
}

export function skyPetStatus(input: SkyPetInput): string {
  if (input.warning.trim()) return '出错了'
  if (input.sessionState === 'CANCEL_REQUESTED') return '停止中'
  if (input.sessionState === 'PAUSED') return '已暂停'
  if (['PRECHECK', 'BUILDING', 'BUILD_VALIDATION', 'SOLVING', 'SOLUTION_VALIDATION', 'REPAIRING', 'REVALIDATING', 'EXPORTING', 'REPORTING'].includes(input.sessionState ?? '')) return '运行中'
  if (input.busy || input.askStreaming) return '思考中'
  if (input.pendingIntent) return '待确认'
  if (input.scientificVerified) return '已完成'
  if (input.sessionState === 'COMPLETED') return '待核验'
  return '待命中'
}
