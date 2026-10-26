# PyInstaller spec for the complete, unchanged Scientific Runtime.
from pathlib import Path
from hashlib import sha256

from PyInstaller.building.datastruct import TOC
from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs, collect_submodules, copy_metadata

root = Path(SPECPATH).parent
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
]
datas.extend(collect_data_files("mph"))
datas.extend(collect_data_files("tzdata"))
datas.extend(copy_metadata("tzdata"))
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
build_only_metadata = {
    "tzdata-2026.4.dist-info/direct_url.json",
    "tzdata-2026.4.dist-info/uv_cache.json",
}
present_build_only = {
    dest.replace("\\", "/") for dest, _source, _kind in analysis.datas
    if dest.replace("\\", "/") in build_only_metadata
}
if present_build_only != build_only_metadata:
    raise RuntimeError("Frozen tzdata build-only metadata membership changed")
analysis.datas = TOC(
    row for row in analysis.datas
    if row[0].replace("\\", "/") not in build_only_metadata
)
# Keep the native TLS and FFI inputs tied to previously traced source builds.
pinned_native = {
    "libffi-8.dll": (
        root.parent / "libffi-build" / ".libs" / "libffi-8.dll",
        "17ACEDB4D86F630438BBA70F349F982A9A387C068C8F0D72F2DD1E73B2C92C49",
    ),
    "libcrypto-3-x64.dll": (
        root.parent / "openssl-sanitized-build" / "openssl-3.5.9" / "libcrypto-3-x64.dll",
        "44A1C07CE88EB12A77FBCB38050B63A80BC61FF4EFB55758136D1A3C7AA8E2BB",
    ),
    "libssl-3-x64.dll": (
        root.parent / "openssl-sanitized-build" / "openssl-3.5.9" / "libssl-3-x64.dll",
        "EBF246E9A5FD0FA7B4B2DA9FCD838AF7B057AC133461DDED035BD18379426F23",
    ),
}
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
