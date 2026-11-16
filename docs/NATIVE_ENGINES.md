# 本机科学软件连接与验收

Windows 下继续使用现有 EngineTaskManager、ScriptRunManager、CapabilityRegistry 和 COMSOL Bridge。配置通过现有桌面 API 和本机运行时数据保存，项目源码不包含个人安装路径。

## 安装发现与版本

MATLAB、Materials Studio 和 LAMMPS 除显式配置、PATH 与常见厂商目录外，也检查 Windows 已登记的安装根。只在这些根下检查固定布局，不递归搜索用户文件。Materials Studio 支持 `<安装>/etc/Scripting/bin/RunMatScript.bat` 和登记根下的版本子目录。

在工作台的 Materials Studio 或 LAMMPS 页面配置实际启动器并点击连接。Materials Studio 应选择 RunMatScript，不选择桌面 GUI。连接时在自有任务目录中执行固定脚本，核对本地回执及厂商重定向输出。握手成功仅登记该安装的脚本能力。服务器版 MatServer 的产品版本单独记录，启动器或服务器二进制变化后需要重新连接。

Materials Studio 20.1 的本机 `PotentialEnergy` API 文档规定能量为 kcal/mol。该版本结果显示相应单位和文档依据；未确认版本继续显示原生单位待核对。其他版本可发现和连接，不因安装路径或名称相似就继承 20.1 的实算结论。

## MATLAB 环境与退出状态

Windows 的固定握手和脚本命令使用 `-wait -batch`，以便等待 MATLAB 并取得实际退出状态。进程环境仅对 MATLAB 放行确切的 `MLM_LICENSE_FILE` 和 `LM_LICENSE_FILE` 两个许可入口，其他密钥、桌面令牌和自定义敏感变量仍过滤。Python、Mathics3 和其他引擎不继承此白名单。

当前机器由用户确认没有安装 MATLAB。本轮完成上述修复的自动化测试，不能据此声称 MATLAB 真实连接、工具箱或另一台电脑的故障已经验证。

## 当前实机证据

2026-11-07，在 `dev/sky-next` 使用正常产品 API 与现有执行器验收。

| 软件 | 实际证据 | 结论范围 |
| --- | --- | --- |
| Materials Studio 20.1.0.2107（MatServer） | 固定脚本握手成功；Universal 水分子单点能量 1.19980617301219 kcal/mol；XSD 三原子、力场类型和能量与产品结果一致；退出与清理通过 | 当前服务器版本的脚本、Forcite 单点及结果读回通过；力场物理适用性仍待科学审核 |
| LAMMPS 22 Jul 2025 Update 4 | 固定种子 108 粒子 LJ，1000 步平衡后 1005 步生产；热力学与 11 帧轨迹读回，退出与清理通过 | 当前 LJ 任务执行与数据通路通过；不认证真实材料性质或扩散机制 |
| Python | 正常脚本 API 得到整数和 5050 及 sqrt(2)，独立数值检查通过 | 固定标准库数值算例通过 |
| Mathics3 | 正常脚本 API 返回 `Simplify[D[Integrate[x^2,x],x]-x^2] = 0` | 这个符号恒等式算例通过，不等同 Mathematica 全兼容 |
| COMSOL 6.3 build 290 | 原有已验证模型副本，经产品 Bridge API 真实启动、加载、模型树及参数读回；原文件未改动，工作台自有 Bridge 退出 | 连接和模型树读回通过，本轮尚未提交 Heat 求解 |
| MATLAB | 用户确认未安装，连接和环境策略测试通过 | 本机真实验收不可执行 |
| VASP 6.4.3 | 前轮同输入 Si 静态 SCF 后端真实回归通过 | 详见既有 VASP WSL 验收，本轮未重复 RUN |

后端全量 1,635 项通过，14 项历史安装/发布测试跳过。前端 113 项通过，生产构建通过。MS、LAMMPS、Python 与 Mathics 的独立产物及数值复核共 19 项通过。

本轮验收报告 · [VASP 已有证据](VASP_WSL.md) · 唯一问题台账

真实长任务取消/崩溃恢复、跨版本 MS 与 MATLAB、COMSOL 6.4，以及打包后的桌面操作与干净机器安装仍需逐项验收。自动化测试、合成进程和真实算例的证据分别保存，未对整个软件集合宣布正式交付完成。
