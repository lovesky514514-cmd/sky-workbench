# SKY Workbench 2.0.0 — public-source review snapshot

This is a **sanitized source snapshot prepared for final release review**, not yet a published release. Owned first-party code is offered under Apache-2.0; publication of the full distribution remains gated. The first-party license, COMSOL integration distribution rights, third-party redistribution obligations, branding rights, and exact security exceptions require owner approval before this repository or its installer may be made public.

SKY Workbench is a local-first Windows scientific modeling workbench. Its React/TypeScript frontend, Rust/Tauri desktop shell, Python scientific Runtime, and Java COMSOL bridge remain separate. The owner selected CC BY 4.0 for licensable original desktop-pet and icon artwork; overall software publication remains gated. This snapshot does not include COMSOL, a COMSOL license, proprietary knowledge-base documents, user models, credentials, or local-model weights.

## Repository map

- `frontend/src`: application UI; `frontend/src-tauri`: Tauri shell and resource declarations.
- `backend/app`: scientific Runtime; `backend/tests`: source-level regression tests.
- `java_bridge/src`: COMSOL-adjacent Java bridge source; requires the user's licensed COMSOL environment to execute.
- `scripts` and `packaging`: build and historical compatibility scripts.
- `frontend/src-tauri/resources/licenses`: unmodified third-party licenses, notices, and covered-source packages. Do not remove these to make a scan pass.

Build status and limitations are in [BUILDING.md](BUILDING.md). The OpenSSL-updated internal Setup SHA-256 is `7C4F1886D4A57C3F9A4DDF7D2F706506C28F19EB975B9477B205CDF1158E8A58`; its sidecar SHA-256 is `CE8E1412E791F68198D3426C7685C27D2208F8AB387534374A21478302DFC183`. The previous Setup and sidecar remain separate historical evidence. This source snapshot corresponds to the new candidate's first-party code and resources, but does not include frozen binaries or prove byte-for-byte reproducibility. It is not authorized for GitHub or public download.

## Licensing and release boundary

Owned first-party code: [Apache-2.0](LICENSE) (Copyright 2026 SuKoYa).
Artwork: [CC BY 4.0 provenance](docs/ASSET_LICENSE.md). Third-party
components retain their own licenses in `frontend/src-tauri/resources/licenses`.
[COMSOL workflow scope](docs/COMSOL_DISTRIBUTION_SCOPE.md) and
[MIT history](docs/HISTORICAL_LICENSES.md).
