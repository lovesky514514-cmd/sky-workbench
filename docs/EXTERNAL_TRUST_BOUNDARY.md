# SKY External Trust Boundary

该子系统从源码目录用 `python -m external_trust` 单独运行。需要 SKY 的 Python 环境及 `PATH` 上受信任的 Node.js。提交的两个 WASM 模块可以直接使用，重建需要 `clang` 和 `wasm-ld`。

## 角色与信任边界

| 角色 | 读入和产出 |
| --- | --- |
| Communication Expert | 固定 WASM 模块，在 `communication` 工作目录运行。规范化纯文本，为提议、接受、拒绝生成受限任务状态建议。 |
| Agent Security Expert | 另一个固定 WASM 模块，在 `security` 工作目录运行。输出提示词注入、恶意工具、记忆污染、活跃内容/地址、敏感标记、跨 Agent 传播六类风险标志。 |
| 网关 | 管理自身 SQLite、文档对象、任务状态、HMAC 凭据、交换传输和审批。对 Worker 输出再次验证。受信任本地操作员使用 CLI，模拟 Agent 只使用回环文档协议。 |

两个 WASM 模块没有 WASI 或任何宿主导入，固定两页线性内存。Node 宿主只加载固定的哈希匹配模块，向其传入有界字节并取回定长状态或有界规范化结果。独立的进程工作目录和过滤后的环境不包含网关凭据或科研凭据。Node 宿主、Python 网关、文件系统权限及本机操作员账户仍属于信任边界。

文档只承载公开纯文本及有限的任务、主题字段。网关独立校验 HMAC、双方身份、消息 ID、有效期、零跳数、字节数和 SHA-256。结构化 JSON 拒绝重复键和额外字段。正文按文本展示，地址、指令和工具名均没有可执行权限。对象先写临时文件，经检查后排他发布，路径只来自摘要。对象再次读取会检查字节、摘要、文件类型及链接。

本轮 A2A 配置支持 `SendMessage` 的单个数据 Part。DSH 配置模拟已登记实例间的文档交接。传输仅连接本地配置的 `127.0.0.1` 端口，固定请求路径，HMAC 挑战在同一连接验证后才交换文档。消息不能指定地址、重定向、回调、工具、转发对象或真实 DSH 会话。入站以持久化游标拉取，同标识同负载幂等，标识冲突拒绝。发送前持久领取一次审批，回执丢失或无效即记为 `DELIVERY_UNKNOWN`、停用该 Agent；恢复检查将过期领取收尾，原消息保持无自动重发。人工重新登记后只能处理新的有效授权。

审批分为外发 `send`、导入 `import` 和知识发布 `update`。操作员先读 `preview` 中的具体文档及绑定，再用返回的摘要批准。批准与执行分开，绑定文档哈希、收件方、来源、用途、权限、有效期、当前授权代次和消息身份。发布还绑定当前知识版本。总开关、Agent 撤销、单份审批撤销、未知提交及每次决策都进入独立审计。

## 本地模拟操作

下列示例在仓库根目录执行。为每个模拟 Agent 使用独立的合成凭据文件。`authorize` 不把密钥写到控制台。选择专用的本地模拟端口和全新目录。

```powershell
$trustRoot = 'E:\sky-external-demo\boundary'
$mockState = 'E:\sky-external-demo\mock-agent'
$pythonExe = '.\.venv\Scripts\python.exe'
New-Item -ItemType Directory -Path $mockState
& $pythonExe -X utf8 -m external_trust --root $trustRoot init
& $pythonExe -X utf8 -m external_trust --root $trustRoot enable
$peerDeadline = [DateTimeOffset]::UtcNow.ToUnixTimeSeconds() + 900
& $pythonExe -X utf8 -m external_trust --root $trustRoot authorize fixture-peer a2a 18187 --scopes send receive import update --expires $peerDeadline --mock-credentials "$mockState\synthetic-credentials.json"
```

在独立终端启动模拟 Agent。可用 `--fixture` 加载一份公开合成的入站 JSON 文档。改用 `dsh` 需另行登记。

```powershell
& '.\.venv\Scripts\python.exe' -X utf8 -m external_trust mock-peer 18187 --credentials 'E:\sky-external-demo\mock-agent\synthetic-credentials.json'
```

主终端可用 `draft fixture-peer` 从标准输入创建公开文本草案，或者 `pull fixture-peer` 拉取已登记模拟 Agent 的文档。输入类型为 `{"classification":"public","kind":"note","title":"Synthetic note","body":"Public fixture."}`。知识文档用 `kind: knowledge` 并提供 ASCII `topic`。

```powershell
$draft = ('{"classification":"public","kind":"note","title":"Synthetic note","body":"Public fixture."}' | & $pythonExe -X utf8 -m external_trust --root $trustRoot draft fixture-peer) | ConvertFrom-Json
$reviewExpiry = [DateTimeOffset]::UtcNow.ToUnixTimeSeconds() + 180
& $pythonExe -X utf8 -m external_trust --root $trustRoot preview send $draft.id --expires $reviewExpiry
# 人工阅读上述文档和审批绑定后，填入返回的摘要。
$reviewedHash = 'paste-confirmation-sha256-here'
$approval = (& $pythonExe -X utf8 -m external_trust --root $trustRoot approve send $draft.id --expires $reviewExpiry --confirm $reviewedHash --operator local-operator) | ConvertFrom-Json
& $pythonExe -X utf8 -m external_trust --root $trustRoot send $approval.approval_id
```

入站知识的流程是 `pull`、`preview import`、`approve import`、`import <approval-id>`，然后单独 `preview update`、`approve update`、`publish <approval-id>`。候选只读，`knowledge` 列出候选和版本，`inspect <document-id>` 查看内容及审查结果。`training-manifest` 只列出经复核的已批准版本摘要，作为将来的离线训练接口。`status`、`audit`、`recover`、`revoke`、`revoke-approval`、`disable` 用于本地运维。

## 验收范围

本地模拟的授权身份是运行 CLI 的本机操作员，`--operator` 是审计标签。凭据保护依赖受信任的本机账户及目录权限。回环 HMAC 不提供生产网络机密性、PKI 或企业用户认证；审计哈希链无远端签名锚。风险规则仅用于筛查，无法证明识别所有注入、污染或敏感内容。正式环境的真实 DSH/A2A 接入、证书信任、跨账户隔离、持续负载和独立安全评估尚未完成。独立 CLI 不被桌面安装包自动启动，也没有科研 Runtime 控制入口。核心科研审批和账本使用原有机制。
