export type AgentMode = 'ASK' | 'PLAN' | 'RUN'
export interface IntentParameter { name: string; symbol: string; value: number | string | null; unit: string; original: string; resolved: boolean }
export interface IntentBoundary { location: string; condition: string; value: number | null; unit: string; resolved: boolean }
export interface ScientificIntent {
  mode: 'PLAN'; language: 'zh-CN' | 'en-US'; goal: string; physics: string[]; geometry: string[]; materials: string[]
  parameters: Record<string, IntentParameter>; boundary_conditions: IntentBoundary[]; initial_conditions: string[]; study: string[]
  outputs: string[]; constraints: string[]; unknowns: string[]; assumptions: string[]; validation_issues: string[]
  confidence: 'high' | 'needs_confirmation' | 'missing_information'; canonical_query: string; original_text: string
}
export type AgentState = 'IDLE' | 'RESEARCHING' | 'PLANNING' | 'PLAN_READY' | 'PRECHECK' | 'WAITING_FOR_EXECUTION' | 'BUILDING' | 'BUILD_VALIDATION' | 'SOLVING' | 'SOLUTION_VALIDATION' | 'VALIDATING' | 'VALIDATION_FAILED' | 'REPAIRING' | 'REVALIDATING' | 'EXPORTING' | 'REPORTING' | 'PAUSED' | 'COMPLETED' | 'FAILED' | 'CANCEL_REQUESTED' | 'CANCELED' | 'INTERRUPTED'
export interface Project { id: string; name: string; path: string; read_only: boolean; active: boolean }
export interface ImportedMph {
  id: string; original_filename: string; size_bytes: number; sha256: string
  imported_at: string; storage_relative_path: string
  preview_state: string; scientific_validation_passed: false
}
export interface ImportedMphPreview {
  preview_state: 'starting' | 'verified' | 'unavailable'
  model_state_verified: boolean; scientific_validation_passed: false
  reason?: string; comsol_version?: string
  model_tree?: Record<string, unknown>
}
export interface TreeNode { name: string; path: string; kind: string; children?: TreeNode[] }

export interface Citation {
  document_id: string; file: string; file_name: string; title: string; page?: number | null
  page_status?: 'precise' | 'page_unknown' | 'invalid'; section?: string | null; chunk_id: string; unit_id?: string | null; excerpt?: string; source_type: string
  ingestion_time?: string; authors?: string[]; doi?: string | null; year?: number | null
}
export type ParameterStatus = 'Benchmark-defined' | 'Verified' | 'Literature' | 'COMSOL Default' | 'User Provided' | 'Assumption' | 'Unknown'
export interface ScientificParameter {
  name: string; symbol: string; value: number | string; unit: string; source: string
  source_page?: number | null; source_chunk?: string | null; confidence?: number | null
  assumption: boolean; verified: boolean; status: ParameterStatus
  original_value?: number | string | null; original_unit?: string | null
  normalized_value?: number | string | null; normalized_unit?: string | null
}
export interface ValidationCheck { category: string; name: string; status: 'passed' | 'failed' | 'warning'; message: string; observed?: unknown; expected?: unknown }
export interface ExecutionVerdict { readonly schema_version: number; readonly run_id: string; readonly workflow_id: string; readonly command_success: boolean; readonly model_state_verified: boolean; readonly scientific_validation_success: boolean; readonly evidence_contract_passed: boolean; readonly completed: boolean; readonly issues: readonly string[] }
export interface EvidenceArtifactRecord { path: string; size_bytes: number; sha256: string }
export interface EvidenceContract {
  schema_version: number; run_id: string; attempt_id: string; passed: boolean; issues: string[]
  artifacts: Record<string, EvidenceArtifactRecord>
}
export interface ValidationReport { scope: string; passed: boolean; command_success: boolean; model_state_verified: boolean; scientific_validation_success: boolean; evidence_contract_passed?: boolean; checks: ValidationCheck[]; execution_verdict?: ExecutionVerdict }
export type PlanQualityStatus = 'VALID' | 'VALID_WITH_WARNINGS' | 'INVALID'
export interface PlanQualityReport { status: PlanQualityStatus; checks: ValidationCheck[]; errors: string[]; warnings: string[] }

export interface Plan {
  schema_version: string; plan_id: string; workflow_id?: string | null; title: string; research_goal: string
  physics: string[]; geometry: string[]; materials: string[]; parameters: Record<string, ScientificParameter>
  boundary_conditions: string[]; mesh_strategy: string[]; study: string[]; solver: string[]
  validation: string[]; expected_outputs: string[]; assumptions: string[]; missing_information: string[]
  sources: Citation[]; steps: Array<{ phase: string; label: string; purpose?: string }>; warnings: string[]
  runnable: boolean; source: string; ai_provider: string; ai_model: string
  quality_status: PlanQualityStatus; quality_report?: PlanQualityReport | null
  precheck_summary?: ValidationReport | null; created_at: string; updated_at: string
}

export interface Message {
  role: 'user' | 'assistant'; content: string; mode: AgentMode; created_at: string; provider?: string; model?: string
  citations?: Citation[]; artifacts?: Record<string, string>; evidence_contract?: EvidenceContract; structured?: Plan; visible_reasoning_summary?: string
  validation?: Record<string, unknown>; validation_report?: ValidationReport; command_success?: boolean; model_state_verified?: boolean; scientific_validation_success?: boolean; evidence_contract_passed?: boolean; execution_verdict?: ExecutionVerdict
  run_id?: string
}
export interface TimelineItem {
  id: string; category?: 'LLM' | 'RAG' | 'COMSOL' | 'VALIDATION' | 'SYSTEM' | string; phase: string; label: string
  status: 'running' | 'info' | 'success' | 'warning' | 'error'; tool: string
  input_summary?: Record<string, unknown> | null; output_summary?: string | null; raw_output?: string | null
  event_type?: 'command' | 'model_change' | 'progress' | 'critical_failure' | string; duration_ms?: number | null; fallback: boolean; created_at: string
}
export interface ModelState {
  geometry?: string[]; materials?: string[]; physics?: string[]; parameters?: Record<string, ScientificParameter>
  boundary_conditions?: string[]; selections?: string[]; mesh?: string[]; study?: string[]; solver?: string[]; results?: string[]
  validation?: ValidationReport; sources?: Citation[]
}
export interface Session {
  id: string; title: string; project_id: string; state: AgentState; mode?: AgentMode | null; plan?: Plan | null
  model?: ModelState | null; error?: string | null; created_at: string; updated_at: string; messages: Message[]; timeline: TimelineItem[]
  events?: Array<{ sequence: number; id: string; type: string; payload: Record<string, unknown>; created_at: string }>
}
export interface ExecutionEta {
  schema_version: number; source: 'execution_ledger'; workflow_id: string | null; state: AgentState
  status: 'unknown' | 'estimated' | 'complete'; estimated_remaining_seconds: number | null
  sample_count: number; basis: string; precision: 'unknown' | 'coarse'; historical_median_seconds?: number
}
export interface EnvironmentItem { name: string; status: 'Ready' | 'Warning' | 'Error'; detail: string; path?: string | null }
export interface ModelCapability { chat: boolean | null; streaming: boolean | null; tool_calling: boolean | null; structured_output: boolean | null; context_size: number | null; location: 'local' | 'cloud' }
export interface Provider {
  name: string; label: string; kind: 'local' | 'cloud'; enabled: boolean; status: string; detail: string
  base_url: string; model: string; selected_model: string; available_models: string[]; capabilities: ModelCapability
  model_details: Array<{ name: string; size_bytes?: number | null; size_label: string; parameter_size?: string | null; quantization_level?: string | null; family?: string | null }>
}
export interface ProviderConfig { enabled: boolean; base_url: string; model: string; api_key?: string }
export type RetrievalMode = 'fast' | 'accurate' | 'deep'
export interface ProviderSettings { providers: Record<string, ProviderConfig>; retrieval_mode: RetrievalMode | 'basic' | 'enhanced'; active_provider: string; extensions?: { literature_modeling?: { enabled: boolean; deepseek_enabled?: boolean } } }
export interface LiteratureModelCandidate {
  source_title: string; source_kind: 'text' | 'pdf' | 'arxiv' | 'pmc'; page_number: number | null
  excerpt: string; intent: ScientificIntent; status: 'review_required' | 'insufficient_evidence'
  can_open_plan: boolean; requires_user_decision: true; modeling_started: false
  scientific_validation_passed: false; warning: string | null
  source_url?: string; publication_date?: string; extraction_method?: string
}
export interface ProviderTestResult { success: boolean; provider: string; status: string; detail: string; model: string; reply?: string; latency_ms: number }
export interface ProviderAcceptanceResult extends ProviderTestResult { tests: Array<{ name: string; label: string; success: boolean; detail: string; latency_ms: number }> }
export interface JavaBridgeEvent {
  sequence: number; event_type: 'command' | 'model_change' | 'progress' | 'critical_failure' | string
  phase: string; status: string; detail: string; created_at?: string
  command_success?: boolean; model_state_verified?: boolean; scientific_validation_passed?: boolean
  target?: string | null; action?: string | null; verified?: boolean | null
  old_value?: string | null; new_value?: string | null; detection_method?: string | null
  progress?: number | null; percent?: number | null; message?: string | null; progress_source?: string | null; duration_ms?: number | null
}
export interface JavaBridgeStatus {
  status: string; bridge_running: boolean; comsol_api_loaded: boolean; model_connected: boolean; connected: boolean
  real_comsol: boolean; detail: string; base_url: string; comsol_version: string; model_tag: string
  bridge_mode: string; connection_mode: string; detection_method: string; poll_interval_ms: number
  process?: Record<string, unknown>; environment?: Record<string, unknown>
}
export type InspectorTab = 'environment' | 'live' | 'overview' | 'geometry' | 'materials' | 'physics' | 'boundaries' | 'mesh' | 'solver' | 'parameters' | 'validation' | 'sources'

// RC4.2 is a read-only projection of authoritative runtime facts. Missing fields stay missing in the UI.
export interface RuntimeDomain {
  contractVersion: 'rc42-v1'
  studyId: string
  modelId: string
  modelSnapshot: Record<string, unknown> | null
  objectIds: { geometry: string; material: string; physics: string; mesh: string; solver: string; boundaries: Record<string, string> }
  modelComponents: {
    geometry: { id: string; length?: { value: number; unit: string } | null; height?: { value: number; unit: string } | null; verification: string }
    material: { id: string; name?: string | null; verification: string }
    physics: { id: string; interface?: string | null; verification: string }
    boundaries: Array<{ id: string; location: string; value?: { value: number; unit: string } | null; verification: string; selectionReadback?: number | number[]; expressionReadback?: string }>
    mesh: { id: string; verification: string; nodeCount?: number; elementCount?: number }
    solver: { id: string; verification: string; degreesOfFreedom?: number }
  }
  runState: { runId: string; attemptId: string; phase: string; headline: string; pauseCapability: string; safePointState: string; estimatedSecondsRemaining?: number } | null
  reviewRequest: { id: string; reviewId: string; runId: string; attemptId: string; objectId: string; objectLabel: string; current: string; proposed: string; unit: string; reason: string; impact: string; state: string; options: Array<{ id: string; label: string; consequence: string }> } | null
  resultArtifacts: Array<{ id: string; runId: string; attemptId: string; title: string; evidenceState: string; metadata?: Record<string, unknown> }>
  evidenceState: string
  authoritativeVerdict: ExecutionVerdict | null
  contractGaps: string[]
}
