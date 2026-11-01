# Windows 3.1 安装与源码边界

桌面产品版本为 3.1.0。最终安装包名为 `SKY_Workbench_3.1.0_x64_setup.exe`，文件身份见[验证记录](VALIDATION_3.1.md)，获取说明见[官网版本页](https://lovesky514514-cmd.github.io/scientific-workbench-beta/releases/3.1/#get-version)。本源码仓库不托管 exe 或预编译科学 Runtime。

安装完成页可创建桌面快捷方式，开始菜单和桌面入口名为 SKY Workbench，主窗口标题为 SKY Workbench 3.1。新配置可默认选择 DeepSeek；已有设置不会被该默认配置钩子覆盖。当前安装包尚未代码签名。目标电脑的实际安装、升级、重启、卸载及交互体验需分别验证。

COMSOL 和其他求解器需用户自行合法安装并授权；安装包不包含厂商 JAR、模型权重、私有项目、知识库或 API Key。从源码构建见 [BUILDING.md](../BUILDING.md)，AI 与求解器范围见[兼容说明](PROVIDER_AND_SIMULATOR_COMPATIBILITY.md)。
