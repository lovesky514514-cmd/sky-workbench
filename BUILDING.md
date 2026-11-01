# 从源码构建

本仓库是 3.1 源码，不附带 COMSOL 文件或模型权重。完整 Windows 11 x64 桌面构建需要 Node.js、Rust/Cargo、Python 3.12、Windows 构建工具和 Tauri/NSIS 依赖；科学执行另需用户单独安装并授权求解器。

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

`packaging/scientific-runtime.spec` 对 libffi、OpenSSL 原生输入及 SHA-256 作精确约束，清单为 `packaging/native-inputs.json`。本轮已在独立目录恢复匹配的三个输入并生成离线输入包；它们没有替换成宿主机碰巧可用的 DLL。构建者需取得同哈希输入，放入 `packaging/native/`。来源见 `docs/OPENSSL_BUILD_PROVENANCE.md` 及本轮构建证据。

目前没有证明另一台机器可以逐字节复现 sidecar 或 Setup；本轮证明的是同一候选源码与输入的构建绑定。更换原生输入后，必须重新审查载荷、许可证与实际运行结果。历史测试不认证新构建。

## 3.1 固定输入与验证

独立 Python 3.12 x64 环境使用 `packaging/requirements-win-py312.lock`，包含 57 个发行包及 wheel 哈希。开发范围声明不代替这个 Windows 构建锁。

```powershell
python -m venv .venv
.venv/Scripts/python.exe -m pip install --require-hashes --only-binary=:all: -r packaging/requirements-win-py312.lock
.venv/Scripts/python.exe scripts/verify_build_inputs.py
.venv/Scripts/python.exe scripts/verify_license_manifest.py
.venv/Scripts/python.exe scripts/run_verification.py --suite backend --out ../verification/backend
.venv/Scripts/python.exe scripts/run_verification.py --suite backend-targeted --out ../verification/targeted
.venv/Scripts/python.exe scripts/run_verification.py --suite frontend --out ../verification/frontend
.venv/Scripts/python.exe scripts/run_verification.py --suite frontend-build --out ../verification/frontend-build
.venv/Scripts/python.exe scripts/build_public_sidecar_review.py --python .venv/Scripts/python.exe --out packaging/build/review01
```

每次输出目录必须全新。验证期间不修改源码。JSON 记录完整命令、选择集、源码文件哈希、日志哈希与前后摘要，不能把旧日志计入本轮结果。

检查冻结程序的 `--native-self-test-report <新报告路径>`，确认原生模块、FFI callback、OpenSSL 3.5.9、时区数据和冻结包内路径。再以独立 `--workspace-dir/--app-data-dir/--kb-dir` 启动 API，检查 3.1.0、公共知识 8 条、项目隔离与退出。私有 COMSOL 资料不进入发行包。

```powershell
./scripts/build_v3_public.ps1 -FrozenSidecarPath <本轮exe> -ExpectedSidecarSha256 <64位大写SHA256> -TargetDir <工作区内新目录>
```

该入口生成标准名称的 NSIS 安装包；默认产品标识与原产品一致，不能声称自动保留独立的 3.0 注册安装。升级保留项目、重启与实际 UI 需要目标电脑验收。不要将 `NOTICE_COLLECTION_VERIFIED` 当成独立法律批准；最终分发审批与实际 Setup 载荷检查分别记录。许可证清单保留 `public_release_approved=false`，不会用内部构建成功代替批准。

## TLS 附属数据

本轮构建解释器的 SSLContext 使用附属证书策略数据。`packaging/native-inputs.json` 同时固定该实现与 `packaging/tls/codex-rejected-cas.pem` 的摘要，spec 会按需要收集公开数据文件；它不是私钥。更换解释器后需核对其实现及实际 TLS/云推理，不可关闭证书检查来绕过问题。
