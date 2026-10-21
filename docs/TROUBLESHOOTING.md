# Help and troubleshooting / 帮助与查错

This guide is for the current V2.0 internal candidate. Use the application's observed status and logs; do not infer a scientific PASS from an installation or health check.

| Symptom / 现象 | Check and safe action / 检查与处理 |
| --- | --- |
| Runtime unavailable / 运行时不可用 | In the app, open **Open Logs**, then use **Restart Runtime**. Preserve the session and logs. If an active run was interrupted, it is not certified as completed. |
| COMSOL unavailable / COMSOL 不可用 | Confirm a separate COMSOL 6.3 installation and valid license. Re-run the environment check. A detected executable alone does not prove Bridge attachment or model readback. |
| Java Bridge disconnected / 桥接未连接 | Confirm COMSOL availability, then inspect the Bridge status and diagnostics. Do not bypass local authentication or treat a reachable port as a verified model. |
| Ollama/model unavailable / 模型不可用 | Check the local Ollama service and the selected model in onboarding or **Settings → Model Center**. Downloads require explicit confirmation; no silent model fallback should be assumed. |
| Plan needs review / 计划需审核 | Check units, geometry, material, boundaries, selection identities, assumptions, and required validation checks before approving. Approval is not scientific certification. |
| Pause or stop appears delayed / 暂停或停止延迟 | Pause waits for a verified safe boundary. Stop ends the attempt; wait for the visible cleanup outcome and inspect logs if cleanup is unverified. Do not manually kill unrelated COMSOL/Java processes. |
| Result unavailable or unverified / 结果不可用或未验证 | Confirm the current session, run, attempt, evidence state, and artifact integrity. A previous attempt's image or a present file cannot certify the current run. |
| Imported MPH cannot be modified / 导入模型无法修改 | Keep the original. Current support does not cover arbitrary MPH import-and-edit. Use a reviewed copy only when the specific workflow is supported, then require fresh readback and validation. |

## Collect a useful diagnostic report / 收集诊断信息

1. Record the product version, Windows version, and which capability shows **Ready**, **Degraded**, **Blocked**, or **Unavailable**.
2. Record the task/session and run/attempt IDs only if sharing them is safe. Note the exact stage and whether the failure happened before plan approval, during build/solve/export, or during result review.
3. Use the application's **Open Logs** action. Share the smallest relevant excerpt, with API keys, tokens, private model data, local account names, and absolute user paths removed.
4. Do not attach a private MPH model, private knowledge base, or license material without a separate, explicit sharing decision.

中文：提供产品/Windows 版本、环境状态、失败阶段和脱敏日志即可；请勿公开上传 API Key、Token、私人 MPH、知识库或本机用户路径。

For installation prerequisites and the current distribution boundary, see [Windows installation](INSTALL_WINDOWS.md). For build limitations, see [BUILDING.md](../BUILDING.md). Contact: [swbtest001@yeah.net](mailto:swbtest001@yeah.net).
