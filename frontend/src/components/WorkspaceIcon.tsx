export type WorkspaceIconName = 'project' | 'geometry' | 'materials' | 'physics' | 'boundary' | 'mesh' | 'solver' | 'results' | 'evidence' | 'task' | 'inspect' | 'menu' | 'close' | 'chevron' | 'play' | 'pause' | 'check' | 'warning'

export function WorkspaceIcon({ name, size = 18 }: { name: WorkspaceIconName; size?: number }) {
  const common = { fill: 'none', stroke: 'currentColor', strokeWidth: 1.7, strokeLinecap: 'round' as const, strokeLinejoin: 'round' as const }
  const paths: Record<WorkspaceIconName, React.ReactNode> = {
    project: <><path d="M4 7.5h6l1.7 2H20v8.5a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2z" /><path d="M4 8V6a2 2 0 0 1 2-2h4l2 2h6a2 2 0 0 1 2 2" /></>,
    geometry: <><path d="m12 3 8 4.5v9L12 21l-8-4.5v-9z" /><path d="m4.4 7.7 7.6 4.4 7.6-4.4M12 12v9" /></>,
    materials: <><path d="m12 3 8 4-8 4-8-4z" /><path d="m4 12 8 4 8-4M4 17l8 4 8-4" /></>,
    physics: <><circle cx="12" cy="12" r="2.2" /><path d="M4.5 9.5c2-4 5.2-6.1 7.2-4.9 2 1.2 2.1 5.3.1 9.3s-5.2 6.1-7.2 4.9c-2-1.2-2.1-5.3-.1-9.3Z" /><path d="M19.5 9.5c-2-4-5.2-6.1-7.2-4.9-2 1.2-2.1 5.3-.1 9.3s5.2 6.1 7.2 4.9c2-1.2 2.1-5.3.1-9.3Z" /></>,
    boundary: <><rect x="4" y="4" width="16" height="16" rx="2" /><path d="M4 9h4M4 15h4M16 4v4M16 16v4" /></>,
    mesh: <><path d="M4 5h16v14H4zM4 12h16M9 5v14M15 5v14M9 12l6-7M9 12l6 7" /></>,
    solver: <><path d="M5 6h9M5 12h14M5 18h7" /><circle cx="17" cy="6" r="2" /><circle cx="9" cy="18" r="2" /><circle cx="12" cy="12" r="2" /></>,
    results: <><path d="M5 19V9M12 19V5M19 19v-7" /><path d="M3 19h18" /></>,
    evidence: <><path d="M7 3h8l4 4v14H7z" /><path d="M15 3v5h4M10 13l2 2 4-5" /></>,
    task: <><path d="M5 5h14v14H5z" /><path d="M8 9h8M8 13h5" /></>,
    inspect: <><circle cx="10.5" cy="10.5" r="6.5" /><path d="m16 16 5 5" /></>,
    menu: <><path d="M5 7h14M5 12h14M5 17h14" /></>,
    close: <><path d="m6 6 12 12M18 6 6 18" /></>,
    chevron: <path d="m9 5 7 7-7 7" />,
    play: <path d="m8 5 11 7-11 7z" />,
    pause: <><path d="M8 5v14M16 5v14" /></>,
    check: <path d="m5 12 4 4L19 6" />,
    warning: <><path d="M12 4 3 20h18z" /><path d="M12 9v5M12 17h.01" /></>,
  }
  return <svg aria-hidden="true" className="workspace-icon" width={size} height={size} viewBox="0 0 24 24" {...common}>{paths[name]}</svg>
}
