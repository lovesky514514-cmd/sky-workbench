<p align="center"><img src="frontend/src/assets/brand/workbench-icon-256.png" alt="SKY Workbench" width="96"></p>

# SKY Workbench 3.0

面向 COMSOL 科学工作流的本地桌面助手：从自然语言计划、人工确认，到模型读回、求解证据与结果检查。命令执行成功、模型状态已验证、科学验证通过始终独立显示。

**本仓库发布源码，不提供 Windows 安装包或预编译 Runtime。** 用户需自行安装并合法使用 COMSOL；本仓库不包含 COMSOL 本体、API JAR、许可证、模型、私有知识库、API Key 或模型权重。SKY Workbench 是独立项目，与 COMSOL AB 无隶属或背书关系。

## 从哪里开始

- [中文使用帮助](docs/USER_GUIDE_V3_CN.md)：首次引导、AI 提供商、建模、结果与代码工作区。
- [故障排查](docs/TROUBLESHOOTING.md)：启动、WebView2、软件扫描、Bridge、Ollama 和云端 API。
- [功能兼容范围](docs/PROVIDER_AND_SIMULATOR_COMPATIBILITY.md)：哪些软件只是被检测到，哪些路径已有执行与证据。
- [从源码构建](BUILDING.md)：依赖、构建入口和当前不可复现的二进制边界。
- [3.0 发行说明](RELEASE_NOTES_3.0.0.md)。

React/TypeScript 实现界面，Tauri/Rust 管理桌面进程，Python 承担 Scientific Runtime，Java Bridge 贴近用户本机的 COMSOL API。主要目录是 `frontend/`、`backend/`、`java_bridge/`、`packaging/` 和 `scripts/`。

COMSOL 6.3 有历史真实执行证据；**这些历史结果不认证新构建，也不证明 6.2、6.4 或其他版本已通过科学验收**。MATLAB、Wolfram、Materials Studio 的安装检测或脚本握手，也不等于这些软件已有模型读回与科学认证。脚本工作区的输出默认未认证。

桌面产品版本是 3.0.0；Scientific Runtime 保留独立的 `1.1.0-rc5.2` API 版本，两者的版本契约由源码测试检查。

## 许可与联系

项目拥有权利的第一方代码采用 [Apache-2.0](LICENSE)。历史 MIT 声明见 [HISTORICAL_LICENSES.md](docs/HISTORICAL_LICENSES.md)；自有图像素材的授权范围见 [ASSET_LICENSE.md](docs/ASSET_LICENSE.md)。第三方许可证、NOTICE 与需提供的源码位于 `frontend/src-tauri/resources/licenses/`，并保留原权利人的声明。COMSOL 集成范围见 [COMSOL_DISTRIBUTION_SCOPE.md](docs/COMSOL_DISTRIBUTION_SCOPE.md)。

维护者：SuKoYa · [swbtest001@yeah.net](mailto:swbtest001@yeah.net)
