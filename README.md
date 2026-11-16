<p align="center"><img src="frontend/src/assets/brand/workbench-icon-256.png" alt="SKY Workbench" width="96"></p>

# SKY Workbench 4.0

4.0.0 源码与构建说明在本仓库公开。Windows 安装包用于内部部署和验收。功能及验证范围见[官网 4.0 页面](https://lovesky514514-cmd.github.io/scientific-workbench-beta/releases/4.0/)。

4.0 由 `ResearchController` 统一组织科研任务，复用 N01 契约、N02 调度隔离、N03 独立审核、N04 定向返工以及既有执行器、RUN 审批与运行账本。真实模型专家通过受限证据工具协作；确定性检查由程序完成，Recovery 只负责诊断和受控提案。实现与调用链见[4.0 架构记录](docs/ARCHITECTURE_4.0.md)。

四组同预算研究对照共发出 60 次真实 DeepSeek 请求，测量补证决策、请求量、Token 和耗时。非标准 Heat 的四次授权内部求解已保存，原 RUN 因独立读回传输错误保留为 `FAILED`。REC-048 修复后另行完成 15 项只读物理复核，原科学认证状态保留。

[SKY External Trust Boundary](docs/EXTERNAL_TRUST_BOUNDARY.md) 提供独立 CLI、A2A/DSH 本地模拟文档同步、隔离审查、人工审批及外部知识版本。当前网络范围为回环模拟 Agent。

面向有限元、分子动力学、第一性原理和数学计算的本地桌面助手：从自然语言草案、候选参数审核，到受控执行、模型读回和结果证据。草案、预检通过、显式 RUN 与求解完成分别判断。

[官方网站](https://lovesky514514-cmd.github.io/scientific-workbench-beta/) · [3.1 更新说明](RELEASE_NOTES_3.1.0.md) · [中文使用帮助](docs/USER_GUIDE_V3_CN.md) · [版本验证记录](docs/VALIDATION_3.1.md)

下述内容保留 **3.1.0 历史基线** 的说明。该版安装包的获取说明、文件名与 SHA-256 见[官网版本页](https://lovesky514514-cmd.github.io/scientific-workbench-beta/releases/3.1/#get-version)；本仓库没有上传 exe 或预编译科学运行时。商业求解器、API JAR、许可证、模型权重、私有项目、知识库和 API Key 不随源码提供。

## 3.1 的主要变化

- 模糊描述继续形成草案，AI 可提出带单位、理由和适用条件的候选值；用户审核后才允许使用，不覆盖原文已知参数。
- DeepSeek 完成真实推理与建模流程验证，适配 JSON 输出和有限重试；保留其他云服务及可选本地模型配置。
- 组织 COMSOL、MATLAB、VASP、Materials Studio、LAMMPS 和 Mathics3 的对应任务。每个引擎分别显示检测、接口、执行及科学检查状态。
- 强化单位、计划修订、审批身份、进程清理、结果文件和证据契约；完善铁块冷却的对流/灰体辐射、独立能量积分和三维资源预估。
- 数学工作区使用 Mathics3；商业 Wolfram/Mathematica 程序入口已移除。高级代码工作区与默认建模审核流程分开。

## 3.1 历史验收范围

本轮工程验收包括 51 份真实 DeepSeek 结构化草案、冻结运行时中的三个实际 COMSOL 6.3 求解案例、受限的 LAMMPS 执行/收集和 Mathics 数学案例。后端 1415 项通过、14 项历史打包辅助项跳过；前端 108 项通过，生产构建通过。

这些结果不代表通用科学建模准确率或所有引擎已完成真实验收。MATLAB、VASP、Materials Studio 缺少可用环境，真实求解未验证；复杂物理、较大模型性能和目标电脑安装/升级/界面仍需分别检查。范围见[兼容说明](docs/PROVIDER_AND_SIMULATOR_COMPATIBILITY.md)与[验证记录](docs/VALIDATION_3.1.md)。

## 从哪里开始

- [从源码构建](BUILDING.md)：依赖、锁定输入、检查与打包边界。
- [故障排查](docs/TROUBLESHOOTING.md)：Runtime、软件扫描、Bridge、云 API 与结果状态。
- [换电脑同步](docs/MODELING_HANDOFF_3.1.md)：源码、项目、环境配置与重新审核。
- [审核处理记录](docs/AUDIT_RESOLUTION_3.1.md)、[四领域设计](docs/FOUR_DOMAIN_WORKBENCH.md)、[致谢](docs/ACKNOWLEDGEMENTS.md)。历史阶段的测试计数不替代最新验证记录。

React/TypeScript 实现界面，Tauri/Rust 管理桌面进程，Python 承担 Scientific Runtime，Java Bridge 接入用户本机 COMSOL API。桌面源码版本为 **4.0.0**，科学 Runtime 使用独立的 `1.1.0-rc5.2` API 版本。

## 许可与联系

项目拥有权利的第一方代码采用 [Apache-2.0](LICENSE)。历史 MIT 声明见 [HISTORICAL_LICENSES.md](docs/HISTORICAL_LICENSES.md)，自有素材授权见 [ASSET_LICENSE.md](docs/ASSET_LICENSE.md)。第三方许可证、NOTICE 与需提供的源码保留在 `frontend/src-tauri/resources/licenses/`。商业软件仍需用户自行合法安装和授权。

SKY Workbench 是独立项目，与 COMSOL AB 无隶属或背书关系。COMSOL 范围见 [COMSOL_DISTRIBUTION_SCOPE.md](docs/COMSOL_DISTRIBUTION_SCOPE.md)。

维护者：SuKoYa · [swbtest001@yeah.net](mailto:swbtest001@yeah.net)
