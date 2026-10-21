<p align="center">
  <img src="frontend/src/assets/brand/workbench-icon-256.png" width="96" alt="SKY Workbench logo">
</p>

<h1 align="center">SKY Workbench</h1>

<p align="center">A local-first scientific workflow assistant for planning, running, and reviewing COMSOL models.</p>

<p align="center">本地优先的 COMSOL 科学工作流助手：从建模计划、受控执行到结果证据复核。</p>

SKY Workbench combines a React/TypeScript interface, a Rust/Tauri desktop shell, a Python scientific runtime, and a Java bridge to a separately installed COMSOL. It keeps command execution, model-state readback, and scientific validation as distinct results. A successful command alone is **not** a verified scientific result.

> **Distribution status:** This repository contains the sanitized SKY Workbench V2.0 source. The Windows installer is provided only through approved internal channels; there is no public EXE download. This repository does not include COMSOL, a COMSOL license, proprietary knowledge-base documents, private models, credentials, or local-model weights.

## Start here

- [Windows installation and first launch](docs/INSTALL_WINDOWS.md) — current internal-candidate instructions; public download is not yet available.
- [User guide](docs/USER_GUIDE.md) — projects, plans, evidence, and safe interruption.
- [Help and troubleshooting](docs/TROUBLESHOOTING.md) — environment checks, logs, recovery, and support information.
- [Build from source](BUILDING.md) — exact toolchain boundary and unverified reproducibility limits.

Users must provide their own valid COMSOL 6.3 installation and license for real COMSOL execution. Local Ollama/model availability is detected, not assumed; missing capabilities are shown as unavailable. The desktop interface can open without COMSOL, but a real COMSOL run cannot be claimed in that state.

## Source layout

| Path | Purpose |
| --- | --- |
| `frontend/src` | Desktop interface and task views |
| `frontend/src-tauri` | Tauri shell, owned-process lifecycle, and release resources |
| `backend/app` | Scientific runtime, validation, and run evidence |
| `java_bridge/src` | Bridge source used with the user's COMSOL installation |
| `scripts`, `packaging` | Build and packaging inputs |

## Licensing and distribution

First-party code is offered under [Apache-2.0](LICENSE) by SuKoYa. Licensable original artwork has separate [CC BY 4.0 provenance](docs/ASSET_LICENSE.md). Historical [MIT notices](docs/HISTORICAL_LICENSES.md) and third-party licenses/NOTICE files remain with their respective components in `frontend/src-tauri/resources/licenses` and must not be removed. [COMSOL integration scope](docs/COMSOL_DISTRIBUTION_SCOPE.md) describes the distribution boundary; this project is not an official COMSOL product or a substitute for a COMSOL license.

The Windows installer remains an internal candidate and is not a public repository asset or Release download. For approved internal testing enquiries, contact [swbtest001@yeah.net](mailto:swbtest001@yeah.net). This source repository does not claim a byte-for-byte reproducible build of the internal installer.
