# SKY Workbench 3.0.0 — 源码发行说明

本次只发布净化源码，不提供 Windows EXE、sidecar、COMSOL 文件或模型权重。

3.0 源码包含环境逐项扫描与可见进度、COMSOL 检测版本展示、可选云端 AI 提供商设置、保留的 Ollama 路径、COMSOL 模型读回入口，以及 Python/MATLAB/Wolfram/Mathics3 的明确边界代码工作区。Materials Studio 当前仅检测与草稿编辑。用户主动执行脚本所得图像不进入 COMSOL 科学认证。

桌面程序的科学结果仍由当前 run/attempt 的命令、模型读回、验证与证据契约分别判定。检测到安装、连接到模型、运行脚本或人工确认，都不自动产生科学成功。

**验证边界：**源码候选完成过定点前端和后端测试；一轮相关后端广覆盖测试留下 3 个测试环境错误，不能写为全绿。曾有基于修复前二进制的 COMSOL 6.3 Heat 真实运行证据；新源码及任何重新编译的安装包都必须独立验收。当前未证明公众可从此仓库逐字节复现内部安装包。请参阅[构建说明](BUILDING.md)和[兼容范围](docs/PROVIDER_AND_SIMULATOR_COMPATIBILITY.md)。
