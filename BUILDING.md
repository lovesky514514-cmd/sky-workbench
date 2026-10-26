# 从源码构建

本仓库是 3.0 源码发布，不附带安装包、预编译 sidecar、COMSOL 文件或模型权重。完整 Windows 11 x64 桌面构建需要 Node.js、Rust/Cargo、Python 3.12、Windows 构建工具和 Tauri/NSIS 依赖；科学执行另需用户单独安装并授权 COMSOL。

## 源码检查

在仓库根目录运行：

```powershell
cd frontend
npm ci
npm run check
npm run build
node --test
```

Python 测试从 `backend/` 运行，依赖见 `backend/requirements*.txt`。Java Bridge 的编译和实际连接需要用户本机 COMSOL 提供的 API JAR，本仓库不分发这些文件。

## 桌面构建边界

Tauri 声明 `scientific-runtime-x86_64-pc-windows-msvc.exe` 为外部二进制。未先由当前源码构建 sidecar 并放入 `frontend/src-tauri/binaries/` 时，`cargo check --locked` 或 Tauri Setup 构建可能在资源检查处停止；不能使用空文件或旧版二进制绕过。

`packaging/scientific-runtime.spec` 对 libffi、OpenSSL 原生输入及 SHA-256 作精确约束；这些输入不在源码根目录中。构建者必须自行取得有来源记录的兼容工具链输入。`scripts/prepare_desktop.ps1` 和 `scripts/build_v3_public.ps1` 只接受明确指定且哈希匹配的当前 sidecar。

目前没有证明另一台机器可以逐字节复现内部 3.0 sidecar 或 Setup；本源码发布不声称可复现二进制构建或新安装包的科学验收。更换原生输入后，必须重新审查载荷、许可证、安全扫描与实际运行结果。历史 2.0 测试不认证 3.0 构建。
