# Build boundary and verification

This V2 review snapshot is the sanitized source base plus the V2 product overlay, separate-window desktop-pet code, and targeted OpenSSL 3.5.9 build-input changes. Internal binary inputs and build logs remain separately held by the release owner. This source tree intentionally contains no frozen sidecar or private model.

## Source-level commands

On Windows 11 x64 with Node, a compatible Rust toolchain, Python 3.12, and access to the locked dependencies:

```powershell
cd frontend
npm ci
npm run build
cd src-tauri
cargo check --locked
```

The `cargo check` command additionally requires a valid `frontend/src-tauri/binaries/scientific-runtime-x86_64-pc-windows-msvc.exe` because Tauri declares an external binary. That binary is deliberately excluded from this source archive. For the new internal candidate, a fresh `npm ci` from the official npm registry, Vite production build, isolated PyInstaller build, and Tauri/NSIS build completed. The exact external OpenSSL and libffi build inputs were checked separately; these results do not prove an independent build from this archive alone.

## Why an exact Setup rebuild is not claimed

`packaging/scientific-runtime.spec` binds libffi to a separately built libffi 3.4.4 DLL and both OpenSSL 3.5.9 DLLs to their exact audited hashes at paths outside this source root. It also pins the official CPython 3.12.14 `ssl.py` source included in `packaging/vendor/` so the distributable does not require an internal build-environment PEM. Their provenance is documented in `docs/OPENSSL_BUILD_PROVENANCE.md` and the internal release evidence; the native binaries are not redistributed in this source snapshot. `scripts/prepare_desktop.ps1` checks the exact new sidecar hash when `SKY_FROZEN_SIDECAR_PATH` is supplied. The isolated Tauri/NSIS build used that path and a fresh Cargo target with user-profile path remapping.

Therefore: this internal candidate was actually built and tested with pinned external inputs, but a third party's byte-for-byte rebuild solely from this archive remains **NOT VERIFIED**. The new binary hash and security exceptions require independent approval before public distribution. Do not treat a successful build as third-party legal authorization.
