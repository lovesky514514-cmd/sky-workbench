# SKY Workbench 4.0 安全攻防测试汇总（公开脱敏版）

内部汇总生成：2026-11-16 17:45 · 测试批次：SKY4-RT-2026-11

本报告根据维护者提供的 DSH 汇总文档编制，**不新增任何未发生的实验，不代表公开 main 已通过本轮安全复测**。
条目按内部轮次编号排列；无法归入轮次的批次单列。下文的 `ROUND*` / `GAP_*` / 其他文件名仅供维护者在**未公开的内部证据档案**中核对，仓库中不包含这些原始记录。

> **公开范围与证据限制**：这是历史冻结副本的脱敏摘要，不是完整逐请求日志或第三方安全认证。原始请求与响应、测试凭据和诱饵标记、机器与用户目录、内部日志及载荷、可直接重放的攻击步骤均不公开。本报告依据用户提供资料整理，未在此处重新执行攻击或核验内部证据哈希。

## 1. 目标与范围

- 产品：SKY Workbench 4.0.0（冻结副本）
- 受测对象：内部冻结 Runtime 与桌面 GUI（具体二进制标识及完整证据由维护者保管）
- 源码树摘要：内部留存；原汇总仅展示截断值，无法公开独立复核
- 二进制与源码同源：**未证实**，作为两个独立对象处理
- 靶场：重定向数据、工作区、知识库和桥接目录的隔离实验环境（内部路径已删除）

## 2. 执行量

| 指标 | 数值 |
|---|---|
| 校准场景 | 492 |
| 语义流程 | 14 |
| Campaign 实际发送用例 | 1042 |
| Campaign 占位（未发送，已剔除） | 8 |
| 累计 sent | 1565 |
| 可判定 adjudicable | 1547 |
| inconclusive | 18 |
| 已确认 | 4 |
| 候选 | 3 |
| 撤销 | 4 |

> 原定 2,000 / 10,000 次目标**未达成**，不以重复请求充数。

## 3. 分轮次日志

| 内部记录标识 | 主题 | 收束 |
|---|---|---|
| `ROUND2_SEMANTIC.md` | 语义攻击轮报告（Semantic Round） | 记录 |
| `ROUND3_KB_PROBE.md` | 第 3 轮：知识库投毒传播链探测记录 | 未验证 |
| `ROUND4_RETRIEVAL_DIVERGENCE.md` | 第 4 轮：检索路径差异定位（KB 投毒链） | 未验证 |
| `ROUND14_CORRECTION_AND_F04.md` | 第 14 轮：撤销 F-03，确认 F-04 | 未验证 |
| `ROUND20_MOCK_BRIDGE.md` | 第 20 轮：Mock Bridge 集成与桥接信任边界首测 | 候选 |
| `ROUND21_BRIDGE_GATE.md` | 第 21 轮：桥接信任边界——握手阶段可测，读回阶段被产品门禁挡住 | 未验证 |
| `ROUND22_EXTERNAL_TRUST.md` | 第 22 轮：External Trust 边界——范围澄清与首个可用测试台 | 记录 |
| `ROUND23_EXTERNAL_TRUST_GATES.md` | 第 23 轮：External Trust 审批与审计链路实测 | 候选 |
| `ROUND24_ET_NEGATIVE_CONTROLS.md` | 第 24 轮：External Trust 负面控制补全 | 记录 |
| `ROUND25_ET_APPROVAL_CHAIN.md` | 第 25 轮：External Trust 审批链完整走通（至传输层） | 记录 |
| `ROUND26_ET_UNKNOWN_STATE.md` | 第 26 轮：失败投递的 UNKNOWN 语义与 peer 离线 | 记录 |
| `ROUND27_ET_E2E_DELIVERED.md` | 第 27 轮：External Trust 端到端投递成功 + UNKNOWN 语义与单次审批验证 | 记录 |
| `ROUND28_SCOPE_ENFORCEMENT.md` | 第 28 轮：scope 越权测试 | 记录 |
| `ROUND29_WORKER_CLAIMS.md` | 第 29 轮：科学可信性边界——worker 自述被降级为"临时主张" | 阻塞 |
| `ROUND30_WORKER_PROBE.md` | 第 30 轮：Worker 层运行时探测（结果不完整，如实记录） | 未验证 |
| `ROUND32_WORKER_NOEXIT.md` | 第 32 轮：Worker 异常后不退出（候选 F-06） | 未验证 |
| `ROUND33_F06_CONFIRMED.md` | 第 33 轮：F-06 定级——无超时的 worker 等待 + 非强制取消不杀进程 | 确认 |
| `ROUND34_ET_WIRE.md` | 第 34 轮：External Trust 传输层伪造探测 | 记录 |
| `ROUND35_WIRE_INVALID.md` | 第 35 轮：传输层负控失败——本轮结论作废 | 记录 |
| `ROUND36_ET_WIRE_VALID.md` | 第 36 轮：传输层校验强度（正控通过，结论有效） | 记录 |
| `ROUND37_FORGED_ACK.md` | 第 37 轮：伪造投递确认（未完成，部分结果） | 记录 |
| `ROUND38_FORGED_ACK_REJECTED.md` | 第 38 轮：伪造投递确认被拒（结论有效） | 阻塞 |
| `ROUND41_RETRACT_BOOKKEEPING.md` | 第 41 轮：撤销"记账瑕疵"结论 + 一条新观察 | 未验证 |
| `ROUND42_F07_BODY_RESET.md` | 第 42 轮：S0404 传输失败归因完成 → 新发现 F-07（Low） | 未验证 |
| `ROUND43_F07_RETRACTED.md` | 第 43 轮：撤销 F-07 —— 现象不随体积单调，实为间歇性重置 | 未验证 |
| `ROUND46_REC048_DECODER.md` | 第 46 轮：REC-048 传输解码器性质测试 | 记录 |
| `ROUND47_LOAD_TRANSFER.md` | 第 47 轮：load_transfer 校验探测（正控未通过 → 结论不予采信） | 记录 |
| `ROUND48_REC048_LOADER.md` | 第 48 轮：REC-048 load_transfer 校验链（正控通过，结论有效） | 记录 |
| `ROUND49_LINK_GUARD.md` | 第 49 轮：传输加载的链接防护（单文件控制通过） | 记录 |
| `ROUND50_FILES_ISOLATION.md` | 第 50 轮：文件隔离实现细节（源码确认 + TOCTOU 缓解） | 记录 |
| `ROUND51_JUNCTION_VERIFIED.md` | 第 51 轮：junction 运行时验证（重解析点防护成立） | 记录 |
| `ROUND53_MCP_ENV.md` | 第 53 轮：MCP 桥接的进程启动面（候选 F-08） | 候选 |
| `ROUND54_F08_UNREACHABLE.md` | 第 54 轮：F-08 可达性判定 —— 被前置存在性门槛挡住 | 记录 |
| `ROUND57_GUI_PREREQ.md` | 第 57 轮：GUI 测试的安全前置——LOCALAPPDATA 重定向已验证 | 记录 |
| `ROUND58_EBWEBVIEW_INFERENCE.md` | 第 58 轮：EBWebView 位置推断（回答第 57 轮遗留问题） | 记录 |
| `ROUND59_GUI_FORMAL_WRITE.md` | 第 59 轮：GUI 探测失败 —— 正式 EBWebView 被写入（我造成的） | 记录 |
| `ROUND69_SANDBOX_GUI.md` | 第 69 轮：Windows Sandbox GUI 隔离环境 —— 建立并验证 | 记录 |
| `ROUND70_SANDBOX_RERUN.md` | 第 70 轮：沙箱探针重跑未取得报告（宿主不变量再次保持） | 未验证 |
| `ROUND71_SANDBOX_NOTRUN.md` | 第 71 轮：沙箱探针连续两轮未执行（原因未隔离） | 记录 |
| `ROUND72_SANDBOX_DIAGNOSIS.md` | 第 72 轮：沙箱诊断 —— 残留实例 + 宿主本身疑似运行在沙箱中 | 记录 |
| `ROUND73_GUI_ENV_VERIFIED.md` | 第 73 轮：GUI 独立测试环境验证完成（可进入动态 GUI 攻击） | 记录 |
| `ROUND74_GUI_ATTACK_GAP.md` | 第 74 轮：动态 GUI 攻击的能力缺口（先讲清，再决定） | 记录 |
| `ROUND75_SANDBOX_PYTHON.md` | 第 75 轮：沙箱内 Python 就绪；但 GUI 攻击的真实瓶颈是 UI 自动化 | 记录 |
| `ROUND81_UIA_WORKS.md` | 第 81 轮：UI Automation 成功枚举 WebView2 内部控件 | 记录 |
| `ROUND82_CHAIN_NOTRUN.md` | 第 82 轮：GUI→Agent 链路探针未启动（报告文件未生成） | 记录 |
| `ROUND83_SANDBOX_ROOTCAUSE.md` | 第 83 轮：沙箱启动故障逐层定位（含一处需精确授权的阻塞） | 记录 |
| `ROUND84_CHAIN_EXECUTED.md` | 第 84 轮：GUI 链路首次完整执行 —— 输入控件已定位并写入，发送未触发模型请求 | 记录 |

## 4. 缺口驱动批次

| 内部记录标识 | 主题 | 收束 |
|---|---|---|
| `GAP_01_CROSSSESSION.md` | 缺口驱动实验 1：跨会话信息边界（有正控的否定结果） | 阻塞 |
| `GAP_BATCH_AB.md` | 缺口驱动批次 A+B：跨会话完整性 与 取消/过期执行 | 守住 |
| `GAP_BATCH_C_CARRIERS.md` | 缺口驱动批次 C：跨会话数据载体的权限模型核实 | 守住 |
| `GAP_BATCH_D_PROJECT.md` | 缺口驱动批次 D：项目级绑定（结论 UNVERIFIED + 一处新候选） | 守住 |
| `GAP_BATCH_E_PROJECT.md` | 缺口驱动批次 E：项目架构、双项目隔离与 F-09 后果 | 守住 |

## 5. 关键事件时间线（按事实发生顺序）

| 事件 | 结果 | 记录 |
|---|---|---|
| 建立隔离靶场并完成校准 | 492 场景，无未处理 5xx 归因 | `CALIBRATION_REPORT.md` |
| 认证边界批量测试 | 130 场景全部 401/403 | `内部证据（未公开）` |
| 供应商端点与凭据绑定边界异常 | **F-02 CONFIRMED（历史候选）**，内部复现细节不公开 | `内部证据（未公开）` |
| 外部检索内容被纳入模型上下文 | **F-04 CONFIRMED**，仅证明上下文摄取，不证明执行门禁被突破 | `内部证据（未公开）` |
| Worker 异常后的等待与回收边界问题 | **F-06 CONFIRMED（仅 Worker 层）** | `ROUND32` / `ROUND33` |
| 特定必要资源缺失时的错误状态问题 | **F-01 CONFIRMED** | `内部证据（未公开）` |
| 检索分叉假设 | **撤销**（我方对照错误） | `ROUND14_CORRECTION_AND_F04.md` |
| 记账瑕疵假设 | **撤销**（漏看 null 行） | `ROUND41_RETRACT_BOOKKEEPING.md` |
| 大 body 致 RST 假设 | **撤销**（非单调） | `ROUND43_F07_RETRACTED.md` |
| 沙箱套沙箱假设 | **撤销**（SID/会话证据否证） | `ROUND72_SANDBOX_DIAGNOSIS.md` |
| GUI 测试曾写入非预期的 WebView 数据（原记录计 58 个文件） | **测试隔离事故，记录保留**；不能据此宣称完全隔离 | `ROUND59_GUI_FORMAL_WRITE.md` |
| GUI 隔离门禁扩大了文件监测覆盖 | 详见内部原始记录，覆盖范围未在此独立核验 | `ROUND60` 相关记录 |
| Windows Sandbox GUI 验证 | 使用沙箱系统账户，宿主监测结果维持不变 | `ROUND73_GUI_ENV_VERIFIED.md` |
| UIA 穿透 WebView2 | 25 个带名按钮可枚举 | `ROUND81_UIA_WORKS.md` |
| GUI 输入写入成功 | `ValuePattern.SetValue ok`，但未产生 `/api/chat` | `ROUND84_CHAIN_EXECUTED.md` |
| Campaign 1050 用例 | 0 新增确认，20 条伪报已分类 | `CAMPAIGN_REPORT.md` |
| 跨会话标题/Plan | **HELD（双正控）** | `GAP_BATCH_AB.md` |
| 双项目隔离（workbench→legacy） | **HELD（单方向正控）** | `GAP_BATCH_E_PROJECT.md` |
| 最终隔离校验 | **12/13**；剩余一项为测试工具会话缓存的诱饵标记残留 | `FINAL_ISOLATION_ATTESTATION.md` |

## 6. 终态分布

| 终态 | 数量 | 条目 |
|---|---|---|
| CONFIRMED | 4 | F-02 High、F-04 Medium、F-06 Medium、F-01 Low |
| CANDIDATE | 3 | F-05、F-08、F-09 |
| RETRACTED | 4 | F-03、runner 记账、F-07、会话在沙箱内 |
| HELD | 11 类 | 认证、协议、输入、跨会话标题/Plan、跨项目单向、完成态迟到取消、ET 审批链、wire 篡改、REC-048、文件隔离 |
| UNVERIFIED | 7 | legacy→workbench、F-06 端到端、GUI 链、bridge 深度、科学判定、私有记忆、旧 attempt |
| N/A_BY_DESIGN | 2 | 知识库项目级、工件 run 级 |
| BLOCKED_BY_SCOPE | 6 | 真实 COMSOL、study run、物理结果一致性、WASM、A2A/DSH/MCP、正式 profile GUI |

## 7. 记录完整性

- 本日志收录的每个文件名均来自磁盘实际存在的记录；缺失的轮次不虚构。
- 撤销项保留原记录，不删除。
- 第 59 轮 GUI 污染事件与最终 12/13 结果均为记录的一部分。

## 8. 修复与独立复验状态（维护者提供的 Codex 回执）

以下是维护者在本次资料提交时提供的**后续修复自测声明**，不属于上述攻击批次的原始证据，也未在此次仓库更新过程中独立复现。修复位于**本地**分支 `security/sky4-remediation-20261111`，提交标识 `2facf0d`；本次编辑时未能在公开 GitHub 仓库确认该提交或分支，因此**不能据此断言公开 `main` 已包含修复**。

| 历史问题 | Codex 报告的修复措施 | 公开验证状态 |
|---|---|---|
| F-02 · High | 凭据绑定包含基本路径的供应商端点，变更端点须重录；覆盖跨源重定向与 HTTPS 降级 | 本地实现与自测已报告；DSH 独立复验待完成 |
| F-04 · Medium | 保留 RAG、引文与授权工具调用，由宿主独立判断审批和执行门禁 | 本地实现与自测已报告；DSH 独立复验待完成 |
| F-06 · Medium | Worker 独立心跳；结合进程树、attempt 归属及是否进入求解器来判定自动回收 | 本地实现与自测已报告；DSH 独立复验待完成 |
| F-01 · Low | 必需契约缺失时返回受控 503，构建与安装时校验资源文件 | 本地实现与自测已报告；DSH 独立复验待完成 |

Codex 报告的修复候选回归：Python **2,251 passed / 14 skipped**；前端 **129 passed**；Rust **5 passed**；禁网 Windows Sandbox **15 passed**。冻结源码、证据和原安装包哈希一致性由 Codex 报告，**本次未收到或复核其完整证据 ZIP**。该修复候选未签名，维护者提供的安装包 SHA-256：`bcf52c8829ceb004e9095606fba9b875306a186a04d1e5c513513910fad4d9e5`；安装包本身未在公开仓库分发。

候选项 **F-05 / F-08 / F-09** 仍为 `CANDIDATE`。真实 COMSOL 求解不属于本轮修复验收。DSH 独立复验尚未完成；沙箱中候选已卸载，但最终关闭状态未确认。上述回归计数与公开主线的构建验证属**不同验收快照**，不得合并计算或相互代替。

## 9. 披露与后续复验要求

- 在修复提交合并至公开分支且对应回归和 DSH 复验完成前，仅将四项问题视为“**已报告修复、尚待独立验证**”，不标为闭环或安全认证。
- 后续公开修复确认须绑定准确的源码提交、测试命令、测试环境、修复后的证据摘要和残余风险；若独立复验失败，应保留失败记录。
- 原始攻击输入、请求/响应、敏感载体、用户数据与独立证据包继续由维护者在隔离环境保存，不上传到公开源码仓库。
