# SKY Workbench user guide / 使用指南

This guide describes the current V2.0 internal candidate. Availability shown by the application is authoritative for the local machine; this document does not certify a particular scientific run.

## 1. First launch / 首次启动

Open the desktop application and review its environment checks. The scientific runtime, COMSOL/Java Bridge, and local model provider are separate capabilities. If a component is unavailable, open the in-app help or [troubleshooting guide](TROUBLESHOOTING.md) before running a task. A valid, separately installed COMSOL 6.3 and license are required for real execution.

启动后先查看环境检测。科学运行时、COMSOL/Java Bridge、本地模型服务各自独立；某一项不可用，不代表其余项也已验证。真实求解需要用户自己的合法 COMSOL 6.3 安装与许可。

## 2. Tasks and plans / 任务与计划

Create a task and enter the scientific request in the task area. Review the generated plan's geometry, materials, physics, boundary conditions, mesh/study, assumptions, and validation checks before approving execution. If a required quantity, unit, or boundary is ambiguous, correct the plan rather than treating a plausible-looking preview as model evidence.

新建任务后输入自然语言要求。执行前逐项核查几何、材料、物理场、边界、网格/研究、假设和验证检查；对不明确的尺寸、单位或选择应先修正再批准。

## 3. Running and interrupting / 执行与中断

The interface distinguishes a reviewed plan from a completed run. During an active run, **Pause** requests a verified safe boundary; **Resume** is available only from a confirmed paused state. **Stop** ends the current attempt and is not a resumable pause. Do not close the application or delete a workspace to simulate cancellation. A new attempt needs new evidence and cannot inherit a previous attempt's certification.

“暂停”会等待已验证的安全点；只有进入已确认的暂停状态才可“继续”。“停止”结束当前尝试，不等于可继续的暂停。新尝试必须重新产生证据，不能沿用旧尝试的认证。

## 4. Results and evidence / 结果与证据

Inspect the result images, model readback, run/attempt identity, artifact integrity, and Model Card together. **Command Success**, **Model State Verified**, **Scientific Validation Passed**, and evidence completeness are separate checks. If evidence is missing, invalidated, or belongs to another attempt, treat the result as unverified even if a PNG or MPH file exists.

结果图片、模型读回、运行与尝试 ID、产物完整性、Model Card 应一起检查。文件存在或命令成功并不等于科学验证通过；证据缺失、失效或属于旧尝试时，当前结果仍是未验证。

## 5. Existing MPH files / 已有 MPH 文件

Preserve the original model. Imported files and newly created projects are distinct in the workspace. A preview or tree readback does not grant permission to overwrite the original, and this candidate does **not** yet provide a complete general-purpose MPH import-and-edit workflow. Only proceed with a supported, reviewed change on a copy, followed by fresh readback and validation.

保留原始 MPH，在副本上审查更改。导入预览不等于可以安全修改任意模型；当前候选版尚无完整的通用 MPH 导入修改流程。修改后需重新读回与验证。

## 6. Data and support / 数据与支持

The in-app help identifies `workspace/models`, `workspace/outputs`, and `workspace/reports` as the model, output, and report areas. Preserve workspace backups before manual file operations. Use **Open Logs** for diagnostics; redact credentials, private models, and local paths before sharing any report. Support contact: [swbtest001@yeah.net](mailto:swbtest001@yeah.net).
