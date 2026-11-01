# 致谢与参考来源

本轮新增接口采用独立实现。参考仓库的设计学习与运行依赖分别记录，商业求解器、LAMMPS、Mathics3 和独立 MPI 测试环境均未加入工作台发行产物。

| 来源 | 本轮参考内容 | 使用情况 |
| --- | --- | --- |
| [LAMMPS](https://github.com/lammps/lammps) 与[官方文档](https://docs.lammps.org/) | 命令行提交、LJ/NVT、thermo 和 dump 格式 | 调用独立安装的引擎；GPL 组件保持独立 |
| [gcnwm/lammps-mcp](https://github.com/gcnwm/lammps-mcp) | 提交与日志读取职责划分 | 设计参考 |
| [XPYY233/auto-lammps-mcp](https://github.com/XPYY233/auto-lammps-mcp) | 运行记录、任务生命周期 | 设计参考 |
| [DrYe1109/MS-MCP](https://github.com/DrYe1109/MS-MCP) | RunMatScript、MaterialsScript 和 Forcite 调用路径 | 接口设计参考；未声明其全部模块已经接入 |
| [VASP 官方文档](https://vasp.at/wiki/index.php/Output) | OUTCAR、OSZICAR、CONTCAR 的职责 | 结果阅读器参考；商业程序与 POTCAR 由用户提供 |
| [AiiDA](https://github.com/aiidateam/aiida-core) | 类型化输入输出与计算溯源 | 设计参考 |
| [jobflow](https://github.com/materialsproject/jobflow) | 任务与输入依赖的组织 | 设计参考 |
| [Mathics3](https://github.com/Mathics3/mathics-core) | 独立数学内核与表达式求值 | 继续使用独立内核；保留其许可边界 |
| [MathWorks 官方命令行文档](https://www.mathworks.com/help/matlab/ref/matlabwindows.html) | MATLAB batch 启动 | 需对应软件与工具箱环境 |

Windows LAMMPS 验证使用 PyPI 的 `lammps 2025.7.22.4.0` 与 conda-forge 的 `msmpi 10.1.1`。MPI 下载包已对照注册表 SHA-256 元数据核验。它们位于工作区研究目录，未添加到产品依赖或安装包。

后续复制任何上游代码或分发新组件时，应保留该组件实际版本的 LICENSE、NOTICE、版权声明和适用义务，并更新发行许可证清单。完整仓库学习清单保存在工作区 `research/interface_learning_20261030` 和 `research/platform_learning_20261030`。

## 3.1 知识库学习与采用

| 来源 | 具体用途 | 采用方式 |
| --- | --- | --- |
| [NetworkX](https://github.com/networkx/networkx) 3.7 | 知识来源谱系图、拒绝循环依赖 | 实际运行依赖；BSD-3-Clause 原文与版权进入许可证清单 |
| [AiiDA](https://github.com/aiidateam/aiida-core)、[jobflow](https://github.com/materialsproject/jobflow) | 项目、任务、输入和来源修订关系 | 设计借鉴，第一方实现 |
| [Haystack](https://github.com/deepset-ai/haystack)、[LlamaIndex](https://github.com/run-llama/llama_index) | 硬过滤、来源元数据与无证据返回 | 设计借鉴，未增加重型框架依赖 |
| 材料/仿真 RAG 与 Agent 项目 | 区分代码许可与内容许可、引擎文档版本、运行证据边界 | 根据本地学习矩阵逐项参考，未把样例语料当科学事实 |

用户提供的 150 仓库索引及补充检索已整理为 `research/knowledge_learning_20261031` 下的 59 份学习快照（46 新克隆、13 复用）。其中许可分类 47 A、11 B、1 C，分类只表示工程复用建议，不能替代具体版本条款。详细来源、路径与采用判断见该目录的学习报告、来源笔记和复用矩阵。引擎内容边界见 [许可矩阵](ENGINE_CONTENT_LICENSE_MATRIX.md)。
