# HTTP 与传输故障的受控恢复

2026-11-07，`dev/sky-next`。扩展现有 SKY Recovery Agent、N02 专家工具代理和 SessionStore，不增加科学任务调度器或 RUN 授权系统。

## 已接入的流程

异常 → 受限事实与脱敏 → 原有语义分类及 HTTP 策略 → 知识库/受限模型诊断 → 提案 → 原有审核或只读核查 → 宿主独立复验。

- `http_contracts.py` 定义操作语义、提交状态、传输错误和决策。只接受结构化事实，不接受任意 URL、Shell、Python 或修复脚本。
- `http_policy.py` 覆盖 64 条 IANA 有名称/保留条目、14 个厂商扩展候选及 15 类传输/运行故障。原有 409 路由、计划、owner、读回和科学证据分支优先。未知 409 继续保持未知，不冒充已定位根因。
- `http_store.py` 在同一个 SessionStore 中保存 HTTP 操作身份、快照/资源摘要、发送次数、截止时间、冷却时间和审计。它不保存第二份科学 RUN 状态。
- `http_execution.py` 只允许白名单 Bridge 读取；同一个 operation 最多三次发送，总期限默认 12 秒，取消、过期、身份或资源变化立即停止。重复打开不重置预算，在途操作重启后拒绝重放。
- Bridge 写参数、删除参数、字段求值和科学 RUN 不再有隐式传输重试。前端不自动重放请求。恢复专家的模型诊断关闭供应商内部不完整 JSON 重试，以免额外发送超出本次逻辑调用。
- 前端保留 HTTP 状态、受限关联 ID、等待秒数和安全类别；204/205/HEAD 不强制解析 JSON，200 中显式 `success=false` 仍作为业务失败。事件流继续使用原有游标、完整帧和取消逻辑。

`FailureEvent` 仍只把 400–599 放进顶层 `http_status`。成功或中间响应的策略由 HTTP 观测契约处理，不能把 202 当成任务完成，也不能凭 200 认定科学结果合格。

## 状态码策略全集

以下是原始候选策略。实际决策继续叠加操作语义、提交状态和权限。500/502/504 在已知只读端点上可进入有界重试；对于 RUN、未知操作和写入，一律先读回，不能重放。完整逐码夹具见 [http_status_policy_v1.json](../backend/tests/fixtures/http_status_policy_v1.json)。

| 候选动作 | 状态码 |
| --- | --- |
| PASS_THROUGH | 100、101、102、103、200、201、203、204、205、206、207、208、226、304 |
| WAIT_AND_POLL | 202、423 |
| REQUIRE_USER | 300、402、407、426、505、507、511 |
| REVALIDATE | 301、302、303、307、308、412、416、428 |
| COMPATIBILITY_ONLY | 104、305、306、418、510 |
| FIX_REQUEST | 400、404、405、406、411、413、414、415、417、422、431 |
| REFRESH_AUTH_ONCE | 401，仅已有合法刷新协议且请求可重放；当前 Desktop/Bridge Token 没有该协议 |
| HARD_STOP | 403、410、451、501、506、508 |
| RETRY_SAFE | 408、425，仅受信只读端点且预算允许 |
| READBACK_FIRST | 409、424、500、502、504 |
| RECONNECT | 421，仅重建只读连接，不代表重新 RUN |
| COOLDOWN | 429、503 |

304 必须有匹配的有效缓存。104 当前注册信息为临时条目，到期日 2026-11-13，实际启用前重新核对；305、306、418、510 保留弃用/未使用/废止标记，不作为自动执行能力。

419、420、444、449、499、520–527、530 均单独处理服务商命名空间，不能由某个响应头自行宣告可信服务商。527 仅保留历史兼容候选。1101/1102 不是四位 HTTP 状态码。未知代码停止自动处理，不推测恢复成功。

传输类型覆盖 DNS NXDOMAIN/SERVFAIL、连接拒绝/重置、证书/握手、connect/read/write/pool timeout、代理认证、SSE 截断、Runtime 崩溃、Bridge 断连及 200 业务失败。分类依靠异常类型和因果链，不解析不可信日志中的“建议”。

## 连接核查与独立复验

`POST /api/recovery/cases/{case_id}/connection-check?session_id=...` 受现有桌面认证、项目和会话作用域约束。前端入口为“只读核查 COMSOL 连接”。

入口通过 N02 的 `recovery.probe_bridge_health` 授权工具访问既有 `/health`，不启动或重启 Bridge、不调用求解、不修改模型。读取有 64 KiB 上限。宿主 `ConnectionVerifier` 在专家返回后结合前后快照检查精确布尔事实；忽略专家或服务自身的 `success`、科学评分和审批声称。观测快照及摘要进入同一事件链，导出可复核哈希。

科学 RUN 的 owner 仍使用原账本。对于手动连接且没有 RUN 的 Bridge，必须同时证明原 owner 槽位为 `bridge`、记录的进程身份仍存活、管理器 PID 一致、模型处于当前可写项目目录且 Bridge 已 ready，才允许只读核查；这不授予写权限。资源摘要变化将中止操作。

连接通过仅记录 `observed_read_only`，不把故障状态改成 RESOLVED。原模拟执行与审批仍独立，只有模拟复验通过时显示“模拟通过，实际故障未确认修复”。

## 提交状态未知时的停止规则

Bridge 在发送科学 RUN 后超时、断线或未取得可信读回时，HTTP 层与上层科学返工循环均停止重放。现有 RunJournal 保存 `UNKNOWN_COMMIT` 清理标志并保留 owner；重启不能因为进程身份缺失就自动释放此状态。

此状态需要人工或后续受控独立读回确认。不要删除 owner 表、假造完成结果或重放旧 RUN 审批。新求解仍只能走原科学审核及显式 RUN 入口。

## 资料、许可证与验证边界

参考 [RFC 9110](https://www.rfc-editor.org/rfc/rfc9110.html)、[RFC 9457](https://www.rfc-editor.org/rfc/rfc9457.html)、[IANA 注册表](https://www.iana.org/assignments/http-status-codes/)以及固定提交的 HTTPX、urllib3、Tenacity、httpx-retries、aiobreaker、Ky、FastAPI Problem Details、RESPX、Authlib、Toxiproxy 和 Temporal 源码。

本轮采用机制与协议设计，没有复制第三方实现或增加产品依赖。独立研究卡保留固定提交、文件哈希及原许可证：HTTPX/aiobreaker/RESPX 为 BSD 类许可，urllib3/httpx-retries/Ky/FastAPI Problem Details/Toxiproxy/Temporal 为 MIT，Tenacity 为 Apache-2.0；Authlib 所读快照含 BSD LICENSE 和另一份商业许可文件。后续实际引入任何实现时须按所用文件保留许可及 NOTICE，不能由本次研读推定已履行再分发义务。

真实验收只覆盖本机 COMSOL 6.3 的连接与模型读回，没有新的科学求解。COMSOL 6.4、其他引擎真实异常恢复、云服务认证刷新、服务级熔断、原生安装包完整操作和爆破测试不在本轮已验证能力中。现有稳定发行副本未修改。
