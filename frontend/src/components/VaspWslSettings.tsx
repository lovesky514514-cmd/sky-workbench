import type { WslVaspProbe, WslVaspProfile } from '../engineTasks'

export const emptyVaspProfile: WslVaspProfile = {
  distribution: '', bin_dir: '', potential_root: '', mpi_path: '/usr/bin/mpirun', variant: 'vasp_std', ranks: 2,
}

export function VaspWslSettings({ zh, profile, distributions, probe, disabled, onChange, onDiscover, onSave, onProbe }: {
  zh: boolean; profile: WslVaspProfile; distributions: string[]; probe: WslVaspProbe | null; disabled: boolean
  onChange: (profile: WslVaspProfile) => void; onDiscover: () => void; onSave: () => void; onProbe: () => void
}) {
  const label = (cn: string, en: string) => zh ? cn : en
  const set = <K extends keyof WslVaspProfile>(key: K, value: WslVaspProfile[K]) => onChange({ ...profile, [key]: value })
  const names = [...new Set([...distributions, ...(profile.distribution ? [profile.distribution] : [])])]
  return <details open>
    <summary>{label('WSL2 本机执行配置', 'WSL2 local execution configuration')}</summary>
    <button type="button" disabled={disabled} onClick={onDiscover}>{label('发现 WSL 发行版', 'Discover WSL distributions')}</button>
    <label>{label('发行版', 'Distribution')}<select value={profile.distribution} disabled={disabled}
      onChange={event => set('distribution', event.target.value)}>
      <option value="">{label('选择已发现的发行版', 'Select a discovered distribution')}</option>
      {names.map(name => <option key={name}>{name}</option>)}
    </select></label>
    <label>{label('VASP 程序目录', 'VASP binary directory')}<input value={profile.bin_dir} disabled={disabled}
      placeholder="/…/bin" onChange={event => set('bin_dir', event.target.value)} /></label>
    <label>{label('本地赝势目录', 'Local potential library')}<input value={profile.potential_root} disabled={disabled}
      placeholder="/…/potentials" onChange={event => set('potential_root', event.target.value)} /></label>
    <label>{label('MPI 启动器', 'MPI launcher')}<input value={profile.mpi_path} disabled={disabled}
      onChange={event => set('mpi_path', event.target.value)} /></label>
    <label>{label('VASP 程序', 'VASP variant')}<select value={profile.variant} disabled={disabled}
      onChange={event => set('variant', event.target.value as WslVaspProfile['variant'])}>
      {['vasp_std', 'vasp_gam', 'vasp_ncl'].map(name => <option key={name}>{name}</option>)}
    </select></label>
    <label>{label('MPI 进程数', 'MPI ranks')}<input type="number" min={1} max={32} step={1}
      value={profile.ranks} disabled={disabled} onChange={event => set('ranks', Number(event.target.value))} /></label>
    <button type="button" disabled={disabled} onClick={onSave}>{label('保存 WSL 配置', 'Save WSL configuration')}</button>
    <button type="button" disabled={disabled} onClick={onProbe}>{label('测试 WSL 连接（不求解）', 'Test WSL connection (no calculation)')}</button>
    {probe && <p role="status">WSL {probe.wsl_version} · Ubuntu {probe.identity.ubuntu_version} ·
      {' '}{probe.identity.variant} {probe.identity.vasp_version} · Open MPI {probe.identity.mpi_version}.
      {' '}{label('连接已验证，科学结果待验。', 'Connection verified; scientific results remain unverified.')}</p>}
  </details>
}
