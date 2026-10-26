# Windows 使用与安装边界

SKY Workbench 3.0 当前公开的是**源码**，没有官方公开下载的 3.0 Setup.exe。请勿把旧版安装包改名为 3.0，也不要把非官方二进制当作本仓库的发布资产。

从源码构建请看 [BUILDING.md](../BUILDING.md)。运行桌面程序需要当前 Windows 用户可用的 WebView2 Runtime；科学执行另需用户安装并合法授权 COMSOL。本仓库不提供 COMSOL 安装程序、API JAR、许可证或破解组件，也不提供 Ollama 模型权重。

首次启动时检查环境扫描和明确的 `Unavailable` / `Degraded` 状态，再创建任务。检测到软件只表示发现安装入口；Bridge 连接、模型读回、求解与科学验证各需自己的证据。遇到问题参见[故障排查](TROUBLESHOOTING.md)。分享诊断前请脱敏 API Key、模型与个人路径。卸载程序不应被当作删除用户工作区的办法。
