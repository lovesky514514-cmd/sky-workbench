# PyInstaller inputs for the SKY 3.1 Scientific Runtime.
from pathlib import Path
from hashlib import sha256
import json
import os
import ssl

from PyInstaller.building.datastruct import TOC
from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs, collect_submodules, copy_metadata

root = Path(SPECPATH).parent
native_manifest = json.loads((root / "packaging" / "native-inputs.json").read_text(encoding="utf-8"))
hidden = []
for package in ("uvicorn", "fastapi", "pydantic", "mph", "jpype"):
    hidden.extend(collect_submodules(package))
hidden.extend(["mcp", "mcp.client", "mcp.client.stdio"])
hidden.extend(["_jpype", "_ssl", "_sqlite3", "_bz2", "numpy._core._multiarray_umath",
               "pydantic_core._pydantic_core", "cryptography.hazmat.bindings._rust"])

binaries = collect_dynamic_libs("jpype")
datas = [
    (str(root / "frontend" / "dist"), "frontend_dist"),
    (str(root / "java_bridge"), "java_bridge"),
    (str(root / "vnext" / "3d" / "minimal_3d_contracts.json"), "vnext/3d"),
    (str(root / "backend" / "mathics_worker.py"), "."),
    (str(root / "knowledge" / "public" / "manifest.json"), "public_knowledge"),
    (str(root / "knowledge" / "public" / "units.json"), "public_knowledge"),
]
datas.extend(collect_data_files("mph"))
datas.extend(collect_data_files("tzdata"))
datas.extend(copy_metadata("tzdata"))
# This build interpreter loads a companion from beside ssl.py during every
# SSLContext construction. Keep that policy and its data in the frozen root.
ssl_source = Path(ssl.__file__).read_bytes()
companion = native_manifest["ssl_companion"]
if companion["filename"].encode() in ssl_source:
    companion_path = root / "packaging" / "tls" / companion["filename"]
    if (sha256(ssl_source).hexdigest() != companion["ssl_source_sha256"] or
            sha256(companion_path.read_bytes()).hexdigest() != companion["sha256"]):
        raise RuntimeError("Pinned SSL implementation or companion data changed")
    datas.append((str(companion_path), "."))
license_root = root / "frontend" / "src-tauri" / "resources" / "licenses"
if not (license_root / "LICENSE_MANIFEST.json").is_file():
    raise RuntimeError("Exact candidate license/NOTICE staging is required")
datas.append((str(license_root), "licenses"))

analysis = Analysis(
    [str(root / "backend" / "desktop_runtime.py")],
    pathex=[str(root / "backend")],
    binaries=binaries,
    datas=datas,
    hiddenimports=hidden,
    hookspath=[],
    runtime_hooks=[],
    excludes=["pytest"],
    noarchive=False,
)
# Local wheel provenance and uv cache hints are build-only metadata. The
# exact frozen tzdata version is pinned here so dependency drift fails closed;
# its METADATA, LICENSE and timezone payload remain in the release archive.
analysis.datas = TOC(
    row for row in analysis.datas
    if not (row[0].replace("\\", "/").startswith("tzdata-")
            and Path(row[0]).name in {"direct_url.json", "uv_cache.json"})
)
# Keep the native TLS and FFI inputs tied to previously traced source builds.
native_root = Path(os.environ.get("SKY_NATIVE_INPUT_DIR", root / "packaging" / "native"))
pinned_native = {name: (native_root / name, digest.upper()) for name, digest in native_manifest["files"].items()}
for name, (path, expected_hash) in pinned_native.items():
    if not path.is_file() or sha256(path.read_bytes()).hexdigest().upper() != expected_hash:
        raise RuntimeError(f"Pinned native build input changed: {name}")
    if sum(Path(dest).name.lower() == name for dest, _source, _kind in analysis.binaries) != 1:
        raise RuntimeError(f"Expected exactly one collected native dependency: {name}")
analysis.binaries = TOC(
    (dest, str(pinned_native[Path(dest).name.lower()][0]), kind)
    if Path(dest).name.lower() in pinned_native else (dest, source, kind)
    for dest, source, kind in analysis.binaries
)
pyz = PYZ(analysis.pure)
exe = EXE(
    pyz,
    analysis.scripts,
    analysis.binaries,
    analysis.datas,
    [],
    name="scientific-runtime",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
)
