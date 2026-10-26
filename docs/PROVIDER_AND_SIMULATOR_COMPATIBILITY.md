# AI providers and scientific software compatibility

This document describes source-level capability, not a new certified desktop release.

## AI connections

- Ollama remains optional. First-run setup favors a user-configured cloud provider; an absent local service or model is shown as unavailable, and model download requires explicit confirmation.
- Kimi, DeepSeek, and Doubao are opt-in under **Settings → AI providers → Cloud APIs**. The user must supply an exact model ID and their own API key. Kimi defaults to the China Moonshot endpoint; Doubao defaults to the Ark data-plane endpoint. Billing plans may use different endpoints, so check the account's official documentation before changing it.
- OpenAI, Claude, and Gemini are in the second-level international section. Availability depends on the user's account, network, region, and provider terms. The app does not route around regional restrictions.
- Cloud keys stay in Runtime process memory or an explicitly supplied environment variable. They are not persisted to the settings JSON or sent to the frontend after saving. A restarted Runtime may require key re-entry.
- A provider marked **Configured** has *not* passed a live inference check. **Test Connection** makes one real inference. The four-part acceptance test is separate and may incur provider charges. An explicitly selected provider never silently falls back to a different endpoint.
- Sending a scientific prompt to a cloud API may disclose that prompt and its supplied context to the provider. Local MPH files and COMSOL installation files are not uploaded automatically. AI output remains subject to ScientificPlan validation and the independent execution/evidence gates.

## Scientific software

| Software | Current source capability | Scientific execution claim |
| --- | --- | --- |
| COMSOL 6.3 | Existing Java Bridge and scientific workers; version-specific installation discovery | Requires a real licensed installation, model readback, and per-run validation. Historical tests are not a claim about a new binary. |
| COMSOL 6.2 / 6.4 | Installation detection and API-JAR-specific Bridge compilation cache | **Unverified** until Bridge attachment/readback and supported workflow tests run against each version. API compatibility cannot be inferred from a matching major version. |
| Python | Separate local interpreter discovery and explicit script run | Does not use the bundled scientific Runtime as a Python REPL; script output has no scientific certification. |
| MATLAB | Bounded discovery, explicit `-batch` handshake, and opt-in `.m` script run | A script exit and image export do not provide model readback or a scientific execution adapter. |
| Wolfram / Mathematica | Bounded WolframScript discovery, explicit local handshake, and opt-in `.wl` script run | Mathematica and standalone Wolfram Engine may have different licenses. No notebook/model readback or scientific adapter yet. |
| Materials Studio | Read-only executable discovery | No model readback or scientific execution adapter yet. SDK access and redistribution terms remain unverified. |

Selecting a COMSOL installation root in Settings changes which local API JAR the Bridge discovers; it does not migrate an MPH file or prove a workflow. The Bridge still uses its own per-process credential and Workbench-owned process lifecycle. No COMSOL proprietary JAR, installation or license is distributed by this feature.

`GET /api/simulation-adapters` never launches software. The user-triggered
`POST /api/simulation-adapters/{matlab|wolfram}/probe` runs a fixed expression
in an owned local process with a 90-second limit and discards vendor output.
The result is `HANDSHAKE_PASSED`, a specific failure state, or `UNAVAILABLE`;
`readback=UNVERIFIED` and `execute=UNAVAILABLE` remain unchanged. The Wolfram
probe forces `-local` so it cannot silently use Wolfram Cloud. Materials Studio
remains detection-only because its MaterialsScript invocation depends on the
installed product version and licensed documentation (2026 introduces Python
MaterialsScript, while earlier releases commonly use Perl).

`/api/script-runs` is a separate, user-confirmed scratch-code path for Python,
MATLAB and Wolfram. It owns one bounded child process, discards credential-shaped
environment variables, captures limited output and serves integrity-checked
local raster files. It does not change ASK/PLAN/RUN, the COMSOL Bridge, the
ExecutionVerdict or scientific claims. The script runs under the user's OS
account and is not a security sandbox. Materials Studio remains editor-only.

The common contract is discovery, explicit connection probe and honest
capability reporting. A future vendor adapter must independently implement
model ownership, readback, cancellation, artifact provenance and scientific
checks before the Runtime can route a natural-language plan to that engine.
Existing ASK/PLAN/RUN currently target the COMSOL scientific workflows; a
Wolfram or MATLAB handshake does **not** redirect those tasks.

Official references used when selecting API shapes: [Moonshot API configuration](https://github.com/MoonshotAI/kimi-cli/blob/main/docs/en/configuration/env-vars.md), [Volcengine Ark OpenAI-compatible API](https://docs.volcengine.com/docs/ark/compatible-with-openai-sdk?lang=zh), [Claude Messages API](https://platform.claude.com/docs/en/api/http/messages), [Gemini generateContent API](https://ai.google.dev/api), [COMSOL 6.4 Java API notes](https://doc.comsol.com/6.4/doc/com.comsol.help.comsol/comsol_api_intro.46.02.html), [MATLAB batch startup](https://www.mathworks.com/help/matlab/ref/matlabwindows.html), [MATLAB Engine for Python](https://www.mathworks.com/help/matlab/matlab_external/get-started-with-matlab-engine-for-python.html), [WolframScript local kernel](https://reference.wolfram.com/language/ref/program/wolframscript.html.en), [Wolfram Engine vs Mathematica](https://www.wolfram.com/engine/faq/), [Materials Studio 2026 scripting](https://www.3ds.com/support/documentation/resource-library/t61-2025-materials-studio-2026).
