export interface ResearchStudy {
  case_id?: string
  session_id?: string
  status: string
  calls?: Array<{ task_id: string; status: string; receipt: {
    requests?: number | null; usage_known?: boolean; input_tokens?: number | null
    output_tokens?: number | null; provider?: string; model?: string
  } }>
  outcome?: { scientific_status?: string; issues?: Array<{ kind: string; required: string }> } | null
}
