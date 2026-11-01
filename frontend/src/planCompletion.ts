export interface CompletionSubmission {
  proposal_id: string
  reviewed_digest: string
  expected_revision: number
  values: Record<string, number | string>
  mode: string
  selected_parameters: string[]
  decisions: Record<string, string[]>
  question_answers?: Record<string, string>
}

export type CompletePlan = (submission: CompletionSubmission) => Promise<void>
