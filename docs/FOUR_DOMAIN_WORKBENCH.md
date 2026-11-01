> 此文件保留对应阶段的实现或审核记录；当前版本的验证范围见 [3.1 验证记录](VALIDATION_3.1.md)。

# 四领域工作台：本轮实现与验证

更新：2026-10-31（Asia/Shanghai）。本地分支：`fix/four-domain-workbench`。

本文保留第一轮实现与历史计数。3.1 第二轮结果以 [发行说明](../RELEASE_NOTES_3.1.0.md) 和 [审核处理表](AUDIT_RESOLUTION_3.1.md) 为准；下文历史“47 项”选择集无法重建，不作为 3.1 验收证据。本轮新选择集由 `scripts/run_verification.py --suite backend-targeted` 明确记录。

## 1. 缺少参数时继续形成草案

不完整的意图现在可以进入规划，形成 `DRAFT_NEEDS_INPUT`。候选参数与已接受参数分开存储，提供自己填写、集中确认建议、授权采用本次列出建议三种方式。

- AI 可以提出新参数，并给出理由和适用条件；生成的 JSON 不能自行声明用户已批准。
- 规则解析得到的用户事实保持优先。候选参数不能覆盖已有输入。
- 确认后的建议仍标记为 `Assumption`、`verified=false`，确认记录写入已有 `AssumptionLedger`。
- 接受建议时重新检查单位、数量和计划完整性。无效参数继续显示检查结果，不进入求解。
- 每次保存增加计划修订号，并使旧运行证据失效。已确认的假设与原始输入保留在记录中。
- 无在线模型时，二维稳态铝板传热可提供明确标注的探索示例；瞬态或三维任务不会被转换成这个示例。

Edge 已实际验证：输入“建一个铝板传热模型”→继续生成草案→查看并确认候选条件→通过原有计划预检。此流程没有启动 COMSOL 求解器。

## 2. 四领域入口与模型页面

| 领域 | 入口 | 本轮交付 |
| --- | --- | --- |
| 有限元与连续介质 | COMSOL、MATLAB | 保留原 Java Bridge 与证据查看器；MATLAB 增加二维稳态传热参数表单及 PDE Toolbox 检查模板 |
| 分子动力学 | LAMMPS、Materials Studio | LAMMPS LJ 流体参数表单、输入结构预览、运行、轨迹和热力学曲线；Materials Studio Forcite 单点能任务 |
| 量子与第一性原理 | VASP、Materials Studio | VASP 四类输入文件、POSCAR 结构预览、本地提交、输出解析；Materials Studio 量子模块明确显示待接入 |
| 数学与物理计算 | Mathics3、Python 高级入口 | Mathics3 积分、求导、化简和解方程表单，公式结果置于主区 |

Wolfram/Mathematica 的导航、安装扫描、握手与执行入口已移除。Mathics3 所需的语言语法支持保留。代码编辑器位于高级面板。

原子结构来自导入输入或真实轨迹，二者分别标注。轨迹支持切帧和旋转，曲线支持切换物理量；界面不将输入结构当作求解结果。窄窗口的全局最小宽度溢出也已修复。

## 3. 新引擎任务接口

共同生命周期为预览、确认、启动、状态读取、取消和产物收集。三种任务保持各自的类型化输入：

- `lammps_lj`：FCC 初始结构、单组分 LJ 势、周期边界、NVT、约化单位。
- `vasp_files`：用户提供 `INCAR`、`POSCAR`、`KPOINTS`、合法取得的 `POTCAR`。当前 POSCAR 阅读器支持带元素名、正比例因子的常用格式。
- `ms_forcite_energy`：元素和坐标、枚举力场、固定生成的 MaterialsScript；使用已安装的 `RunMatScript`。

任务记录包含项目、输入、假设、程序路径、运行状态、输出、进程身份与产物 SHA-256。取消与超时复用原有 `OwnedProcess`。运行记录持久化；重启后未结束的任务标为 `INTERRUPTED`，再次运行需要明确提交。产物下载接口重新核对哈希。

引擎路径可在任务页“本机执行程序”配置，也支持 `SKY_LAMMPS_EXECUTABLE`、`SKY_VASP_EXECUTABLE`、`SKY_MS_EXECUTABLE`。任务执行和路径配置继续要求桌面会话认证；普通浏览器开发页面可用于预览和查看记录。

检测到程序、进程正常退出、获得结果和通过科学检查是独立状态。LAMMPS 的 `BASIC_CHECKS_PASSED` 只表示有限热力学值、完成步数和轨迹等基本检查通过。VASP 与 Forcite 结果保留 `NEEDS_REVIEW`，不能仅凭退出码声称收敛或科学结论成立。

## 4. 可维护性

| 文件 | 职责 |
| --- | --- |
| `backend/app/scientific/plan_completion.py` | 候选生成、接受与单位归一化 |
| `backend/app/engines/models.py` | 各引擎任务输入与约束 |
| `backend/app/engines/inputs.py` | 固定模板和启动参数 |
| `backend/app/engines/results.py` | 领域输出读取和基本检查 |
| `backend/app/engines/manager.py` | 运行、取消、恢复和产物完整性 |
| `frontend/src/components/PlanCompletionReview.tsx` | 参数补全与授权交互 |
| `frontend/src/components/EngineTaskWorkspace.tsx` | 参数、配置与运行历史 |
| `frontend/src/components/EngineResultView.tsx` | 结构、轨迹和曲线 |

关键约束写在邻近逻辑的简短注释中，沿用已有代码风格。没有增加装饰性分隔符或文件开头的长篇说明。

## 5. 验证结果

- 后端回归：1005 项通过；10 项依赖缺失旧发布脚本的测试单列排除，另有两个历史测试模块因同类缺失无法收集。
- 最终针对性回归：47 项通过，包含参数来源、单位换算、批准身份、防止覆盖、会话认证、进程取消、恢复、产物篡改与 Windows 批处理路径。
- 前端：91 项通过；TypeScript 检查和 Vite 生产构建通过。
- Windows 验证发现并修复了 MaterialsScript 启动器在含空格、括号路径下的命令引号问题。实际 Forcite API 尚未在商业软件中运行验收。
- LAMMPS：实际运行 1000 步，108 个原子，101 条热力学记录、11 帧轨迹，四类产物哈希已保存。
- Mathics3：独立内核实际计算 `Simplify[D[Integrate[x^2, x], x] - x^2]` 得到 `0`。
- Edge：验证四领域入口、缺参草案与确认、真实 LAMMPS 结果读取、轨迹切帧及曲线切换。

后端有一条测试客户端依赖弃用提示，前端构建有已有 Monaco 编辑器分块体积提示；本轮未升级这些依赖。

### 历史测试缺失范围

两个未收集模块：`test_phase2_completion.py` 依赖缺失的 `rc3_launcher`；`test_rc42_setup_source.py` 依赖缺失的 `scripts/rc42_setup.py`。

10 项排除测试分别为 `test_rc2_productization.py` 的三个旧 preflight 测试、`test_rc41_product_hardening.py` 的四个旧发布测试、`test_rc4_product_polish.py` 的三个旧发布测试，涉及公开快照未包含的 RC2/RC4/RC4.1 启动和打包脚本。当前产品功能测试没有因此改成跳过。

## 6. 仍需完成的验收与扩展

1. MATLAB 安装与对应 PDE Toolbox 许可环境中的真实运行、边界和温度结果验收。
2. VASP 合法运行环境、赝势和真实算例的电子及离子收敛验收。
3. Materials Studio 对应版本和 Forcite 模块实机验收；CASTEP、DMol³ 工作流尚未接入。
4. COMSOL 6.3/6.4 连接保留用户已确认状态，本轮没有重新完成两版本求解案例验收。
5. SSH/Slurm、通用材料力场、跨运行叠图比较与跨引擎物性传递属于后续扩展；本轮已有独立运行历史与假设记录。
6. 本轮交付为本地源码和前端生产构建；没有生成新安装包、替换已安装程序或推送 GitHub。

## 7. 复验入口

在已安装后端依赖的 Python 环境运行：

```powershell
python scripts/validate_lammps_task.py --executable <lmp.exe绝对路径> --output <验收目录>
python scripts/validate_mathics_task.py --python <Mathics3解释器绝对路径> --output <验收目录>
```

真实结果与独立测试环境的位置记录在工作区的 `worklog/IMPLEMENTATION_PROGRESS_20261031.md`。依赖和源码学习来源见 `ACKNOWLEDGEMENTS.md`。
