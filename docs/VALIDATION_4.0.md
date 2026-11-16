# SKY Workbench 4.0 验证记录

2026-11-16，在公开仓库的 Windows x64 干净克隆中复验。受测源码提交为 `f40061c8a254a4dea14dc9455218f28367f191ea`。发行标签另外包含本文、使用指南和更新说明。

## 干净克隆回归

| 检查 | 环境与结果 |
| --- | --- |
| Python | CPython 3.12.14 x64，按哈希锁安装 57 个依赖。2261 项通过，14 项既有跳过。 |
| 前端 | Node 24.19.0、npm 11.17.0。138 项通过，TypeScript 和 Vite 生产构建通过。 |
| Rust | Rust 1.95.0，按 Cargo 锁文件、使用本次冻结 Runtime 暂存后的资源。5 项通过。 |
| 内部知识索引 | 以已有受控索引单独复验 108 文档、14,210 单元及只读状态。公开回归使用合成夹具。 |
| 密钥扫描 | Gitleaks 8.30.1 扫描当前源码和本地克隆中所有可达公开历史。初次扫描命中一处 SQLite 列别名，人工核对为误报。复扫保留仅匹配该行的例外。扫描为基于规则的检查。 |
| 构建输入 | 57 个 Python 锁定版本、三份原生 DLL、八条公开知识单元和七份配套源码通过校验。Cargo 与 npm 锁文件恢复原清单所登记的字节，依赖版本未变。 |

在 PowerShell 中从仓库根运行：

```powershell
python -m venv .venv
.venv/Scripts/python.exe -m pip install --require-hashes --only-binary=:all: -r packaging/requirements-win-py312.lock
$env:PYTHONPATH = (Join-Path (Get-Location) 'backend')
.venv/Scripts/python.exe -X utf8 -m pytest backend/tests tests -q -ra --basetemp "$env:TEMP/sky-4-validation"
.venv/Scripts/python.exe scripts/check_doc_links.py
.venv/Scripts/python.exe scripts/verify_license_manifest.py
gitleaks dir . --redact=100 --max-archive-depth 1 --max-decode-depth 1
gitleaks git . --log-opts='--all --full-history -m' --redact=100 --max-archive-depth 1 --max-decode-depth 1
cd frontend
npm ci
npm run build
node --test --test-isolation=none tests/*.test.mjs
```

扫描目录使用干净源码快照。构建所需的专用原生输入和输出路径见 [构建说明](../BUILDING.md)。公开 CI 的 Rust 步骤需要已验证的内部 Runtime。

## 冻结 Runtime 与既有桌面验收

本轮使用 `scripts/build_public_sidecar_review.py --python .venv/Scripts/python.exe --out packaging/build/audit-20261116`，从干净克隆重新冻结内部 Runtime。输入摘要构建前后一致，均为 `efa034f5be670893d2ef85cd90911a4b58d99e582d4a0eb00d1ad07f31157966`。新 Runtime SHA-256 为 `0d2092c440466c970846408e4473fc279e820a5da0c1784e55416475e7e3e1ea`，`--native-self-test-report` 通过。

在本地回环启动新 Runtime，加载八条公开知识单元。受保护接口匿名请求返回 401，携带本次桌面凭据返回 200，父进程退出后 Runtime 返回 0。查询隔离账本确认零科学 RUN。

以该 Runtime 为冻结输入，在 `frontend` 执行 `npm.cmd run tauri -- build --bundles nsis`。随后从仓库根执行 `cargo test --manifest-path frontend/src-tauri/Cargo.toml --release --locked --offline`。新内部 NSIS SHA-256 为 `5c17f0f47df244ea1974b06269280fcff470b8d0c6ac6c0f0c0a199a4f4fa037`。从实际安装包提取 Runtime 和 certifi 配套源码并复核摘要，旧 certifi 源码档不在该载荷中。这一轮验证覆盖内部包的构建及指定载荷，原生安装流程沿用下段历史验收。

| 本轮记录 | SHA-256 |
| --- | --- |
| Python 回归 | `3a310e8e88ccf72cd1e3321d1d25e6e0e65a292c91e4adc79a6e479fc71f3a0f` |
| 前端测试 | `9eff80cd1128d790d9e060d0cd8a9796ba7eeee8ae2aea86b25fd3d92a414915` |
| Rust 测试 | `336ff391120771cad49d70b2f0bc1853b91e363337bafa08b042c4f6560120fb` |
| 桌面构建 | `6a176793e918ae526abda77e2affaa92aa14a8ac1281783fee04a641ca67c755` |
| Runtime 回环核验 | `9096e585572429563ad58f6d24c34be370cabc457bab43692dddf6608d13b2ca` |
| NSIS 提取核验 | `b329775d0448e87f54ab9405fe92eb8a2451b86d8554e14fcd903663916e5640` |

2026-11-14 Windows 实机验收使用内部候选安装包 `616e100b84ab277bfb3482c794a58109e0f06ef6ce765b4473985780b8f49cf1` 和 Runtime `a4202a4571186c25b48f2ea0cac83243403f63f0622f19af0edf2134f79d508a`。本机完成从 3.1 升级、原生启动、结果读回及卸载。禁网 Windows Sandbox 通过临时配置微软签名的固定版 WebView2，验证同包安装、启动、Python 输出 385 和卸载。该内部记录对应这一对二进制，首次离线安装 Evergreen 的正向用例仍待验。

## 科学运行状态

| 内部记录 | 实测范围 |
| --- | --- |
| 完成 | 2026-11-14，4.0 工作树候选完成 COMSOL 6.3 的 3 × 3 × 20 mm 铁块瞬态冷却。一个 RUN 和一个 attempt 内完成三个网格及一次时间步收紧，共四次内部求解。160 个最终网格顶点的温度范围为 340.5667–340.6270 K。 |
| 独立物理审核 | 上述同一 RUN 的 16 项检查通过，审核 100 分。相对能量残差 0.0009406%，集总参考差 0.0325239%，网格差 0.0008376%，时间步收紧差 0.0052127%。对应上限为 2%、2%、1%、0.2%。结论适用于常物性导热、指定对流及灰体辐射。 |
| 失败 | 2026-11-09 的 5 × 5 × 25 mm 冷却在四次内部求解后发生大型读回帧失败，原账本为 `FAILED / FatalFailure`。REC-048 后续对历史冻结文件做了另行登记的只读复核，15 项检查通过。原 RUN 状态仍为 `FAILED`。 |
| `UNKNOWN_COMMIT` | 已通过确定性回归检查未知提交的停止、保留 owner 和禁止自动重提交逻辑。本次未观测到新的真实未知提交。 |

本次源码复验的科学 RUN 为零。真实记录属于对应工作树和商业求解器环境下的内部验收。原始模型、用户数据和内部二进制由维护者保管。

## 引擎矩阵

| 引擎 | 已有验收范围 |
| --- | --- |
| COMSOL 6.3 | 4.0 三维冷却、真实网格与温度读回及原生证据页。 |
| COMSOL 6.4 | 需按 6.4 安装、Bridge、读回与真实求解分项验收。 |
| VASP 6.4.3 / WSL2 | 历史后端真实 Si 静态 SCF，MPI 两进程，TOTEN −10.65638172 eV。4.0 桌面包中的点击链路待验。 |
| LAMMPS | 历史 108 粒子 LJ 测试的执行、热力学和轨迹读回。复杂力场和物理结论另验。 |
| Materials Studio 20.1 | 历史 Forcite 水分子单点计算和读回。其他版本及求解模块待验。 |
| Python、Mathics3 | Python 具有本轮既有沙盒最小算例。Mathics3 有既有符号恒等式案例。 |
| MATLAB | 本机缺少安装，已有 Windows 启动策略测试。真实桥接和 PDE Toolbox 待验。 |
| 外部智能体 | 本地模拟文档交换、审批和权限隔离回归。 |

[4.0 架构](ARCHITECTURE_4.0.md) · [本地引擎](NATIVE_ENGINES.md) · [VASP WSL](VASP_WSL.md) · [3.1 历史验证](VALIDATION_3.1.md)
