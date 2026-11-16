export type TaskEngine = 'LAMMPS' | 'VASP' | 'Materials Studio'
export type AtomPoint = { id: number; element: string; position: number[] }
export type TaskInputs =
  | { kind: 'lammps_lj'; cells: number; density: number; temperature: number; timestep: number; steps: number; seed: number; equilibration_steps?: number; thermo_interval?: number; dump_interval?: number }
  | { kind: 'vasp_files'; files: Record<string, string> }
  | { kind: 'vasp_wsl'; files: Record<string, string>; potentials: Record<string, string> }
  | { kind: 'ms_forcite_energy'; atoms: Array<{ element: string; x: number; y: number; z: number }>; forcefield: string }
export type EngineTaskPayload = { title: string; project_id: string; inputs: TaskInputs; confirmed: boolean; timeout_seconds: number; review_ticket?: string }
export type WslVaspProfile = { distribution: string; bin_dir: string; potential_root: string; mpi_path: string; ranks: number
  variant: 'vasp_std' | 'vasp_gam' | 'vasp_ncl' }
export type WslVaspProbe = { status: string; wsl_version: number; scientific_validation: string
  identity: { variant: string; vasp_version: string; ubuntu_version: string; mpi_version: string } }
export type WslVaspReview = { variant: string; ranks: number; vasp_version: string
  input_hashes: Record<string, string>; potential_sha256: string }
export type EngineResult = {
  kind: string; units: string; columns?: string[]; series?: number[][]; energy?: number | null
  electronic_iterations?: number | null; scf_converged?: boolean; normal_termination?: boolean
  solver_elapsed_seconds?: number | null; xml_status?: string; error_codes?: string[]
  frames?: Array<{ step: number; atoms: AtomPoint[] }>
  structure?: { atoms: AtomPoint[]; lattice?: number[][] } | null
  checks: Record<string, boolean>; scientific_validation: string; limitations: string
  preview?: { total_frames: number; shown_frames: number; max_atoms_per_frame: number; max_frames: number; full_data: string }
  validation_records?: Record<string, Record<string, unknown>>
}
export type EngineRun = {
  run_id: string; project_id: string; title: string; engine: TaskEngine; state: string; created_at: number
  command_success: boolean; model_state_verified: false; readback_available?: boolean; verification_status?: string; scientific_validation: string
  elapsed_ms: number; result: EngineResult | null; output: string; detail?: string
  assumptions: string[]; request: { inputs: Record<string, unknown> }; artifacts: Array<{ name: string; sha256: string; size_bytes: number }>
  omitted_artifacts?: Array<{ name: string; size_bytes: number | null; reason: string }>
  transport?: 'wsl2'; linux_cleanup_pending?: boolean; vasp_variant?: string; mpi_ranks?: number
  electronic_progress?: { method: string; iteration: number }
}
