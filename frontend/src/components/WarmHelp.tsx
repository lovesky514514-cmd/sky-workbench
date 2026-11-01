import { useEffect, useRef } from 'react'
import type { EnvironmentItem, Provider } from '../types'
import type { Locale } from '../i18n'

type Props = {
  locale: Locale
  environment: EnvironmentItem[]
  providers: Provider[]
  activeProvider: string
  onClose: () => void
  onOpenLogs: () => void
}

export function WarmHelp({ locale, environment, providers, activeProvider, onClose, onOpenLogs }: Props) {
  const zh = locale === 'zh-CN'
  const copy = (cn: string, en: string) => zh ? cn : en
  const comsol = environment.find((item) => item.name === 'COMSOL')
  const bridge = environment.find((item) => item.name === 'Java Bridge')
  const selectedAi = providers.find((provider) => provider.name === activeProvider)
  const closeButton = useRef<HTMLButtonElement>(null)

  // Help can open above the first-run guide; Escape returns to that guide.
  useEffect(() => {
    closeButton.current?.focus()
    const dismiss = (event: KeyboardEvent) => { if (event.key === 'Escape') onClose() }
    window.addEventListener('keydown', dismiss)
    return () => window.removeEventListener('keydown', dismiss)
  }, [onClose])
  return <div className="warm-help-backdrop" role="presentation" onMouseDown={onClose}>
    <section className="warm-help" role="dialog" aria-modal="true" aria-label={copy('工作台帮助', 'Workbench help')} onMouseDown={(event) => event.stopPropagation()}>
      <header><h2>{copy('帮助与排错', 'Help and troubleshooting')}</h2><button ref={closeButton} type="button" onClick={onClose} aria-label={copy('关闭帮助', 'Close help')}>×</button></header>
      <p>{copy('按“检查环境 → 连接 AI → 审查计划 → 运行与核验证据”使用工作台。',
        'Use the Workbench in this order: check environment → connect AI → review plan → run and inspect evidence.')}</p>
      <div className="warm-help-sections">
        <article><strong>{copy('1 · 检查环境', '1 · Check environment')}</strong><span>{comsol?.status === 'Ready'
          ? copy(`设置 → 常规：COMSOL 已检测；Bridge ${bridge?.status === 'Ready' ? '已连接' : '未验证'}。安装检测不代表模型可运行。`,
            `Settings → General: COMSOL detected; Bridge ${bridge?.status === 'Ready' ? 'connected' : 'unverified'}. Discovery does not prove execution.`)
          : copy('设置 → 常规 → 重新扫描；多版本电脑可指定 COMSOL 安装目录。',
            'Settings → General → Scan again; select the COMSOL installation root on multi-version computers.')}</span></article>
        <article><strong>{copy('2 · 连接 AI', '2 · Connect AI')}</strong><span>{selectedAi
          ? `${selectedAi.label} · ${selectedAi.status === 'Ready' ? copy('已连接', 'Connected') : selectedAi.status === 'Configured' ? copy('已配置，连接未验证', 'Configured, connection unverified') : copy('不可用', 'Unavailable')}。${copy('在设置 → AI 提供商中测试连接；Ollama 是可选项。', 'Test it in Settings → AI Providers; Ollama is optional.')}`
          : copy('设置 → AI 提供商：选择云端 API 或本地模型，再测试连接。不要把密钥写进任务描述。',
            'Settings → AI Providers: choose a cloud API or local model, then test it. Never put keys in a task.')}</span></article>
        <article><strong>{copy('3 · 审查计划', '3 · Review plan')}</strong><span>{copy('在“任务”描述几何、材料、物理场、边界及结果；生成计划后核对参数、单位和假设，再确认运行。',
          'In Tasks, describe geometry, materials, physics, boundaries and outputs. Check parameters, units and assumptions before confirming RUN.')}</span></article>
        <article><strong>{copy('4 · 运行与结果', '4 · Run and results')}</strong><span>{copy('运行中可暂停或紧急停止。模型页显示真实读回；结果页只显示当前运行且通过完整性检查的产物；“证据”解释科学状态。',
          'Pause or emergency-stop a run. Model shows real readback; Results shows only current integrity-checked artifacts; Evidence explains the scientific status.')}</span></article>
        <article><strong>{copy('导入 MPH', 'Import MPH')}</strong><span>{copy('任务 → 导入的 MPH：导入副本并预览。保留原件；修改仍需真实读回、人工确认和重新验证。',
          'Tasks → Imported MPH: import a copy and preview it. Keep the original; edits require real readback, approval and revalidation.')}</span></article>
        <article><strong>{copy('四领域工作区', 'Scientific domains')}</strong><span>{copy('模型页按有限元、分子动力学、量子计算和数学计算组织。LAMMPS 提供 LJ 流体任务，VASP 导入四类输入文件，Materials Studio 当前支持 Forcite 单点能。MATLAB 需要实际安装和 PDE Toolbox；Mathics3 使用独立内核。参数与假设经确认后执行，代码位于高级面板，结果保留各自的检查状态。',
          'The Model page groups finite elements, molecular dynamics, quantum and mathematical tasks. LAMMPS offers an LJ fluid task; VASP imports four input files; Materials Studio currently exposes Forcite single-point energy. MATLAB requires an installation and PDE Toolbox; Mathics3 uses an external kernel. Review parameters and assumptions before running; code is in the advanced panel and result checks remain explicit.')}</span></article>
        <article><strong>{copy('故障恢复', 'Recovery')}</strong><span>{copy('先检查设置中的服务状态；Ollama 未启动可改选云端 API。再打开诊断日志。旧任务结果不会认证新任务。',
          'Check service status first; select a cloud API if Ollama is stopped. Then open logs. Old task results never certify a new task.')}</span></article>
      </div>
      <footer><button type="button" onClick={onOpenLogs}>{copy('打开诊断日志', 'Open diagnostic logs')}</button><button type="button" onClick={onClose}>{copy('完成', 'Done')}</button></footer>
    </section>
  </div>
}
