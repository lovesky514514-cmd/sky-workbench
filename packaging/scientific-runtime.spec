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
]
datas.extend(collect_data_files("mph"))
datas.extend(collect_data_files("tzdata"))
# HTTPX constructs its TLS context even for the loopback Java Bridge client.
# The frozen Runtime must carry certifi's CA file, not point into the build venv.
certifi_data = collect_data_files("certifi")
if len([row for row in certifi_data if Path(row[0]).name.lower() == "cacert.pem"]) != 1:
    raise RuntimeError("Expected exactly one certifi CA bundle in the frozen Runtime")
datas.extend(certifi_data)
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
# The build interpreter is CPython 3.12.14 with one vendor-local ssl.py
# modification that loads an internal PEM absent from redistributable Python.
# Replace that single module with the checksum-pinned official 3.12.14 source;
# the standard certificate verification path and bundled certifi stay enabled.
official_ssl = root / "packaging" / "vendor" / "python-3.12.14" / "ssl.py"
official_ssl_hash = "039A0E3F1782E14824B1624C7FFEFC2DE31714B4A9994424BE45A4271966097C"
if not official_ssl.is_file() or sha256(official_ssl.read_bytes()).hexdigest().upper() != official_ssl_hash:
    raise RuntimeError("Official CPython 3.12.14 ssl.py source is missing or changed")
ssl_entries = [row for row in analysis.pure if row[0] == "ssl"]
if len(ssl_entries) != 1:
    raise RuntimeError("Expected exactly one Python ssl module")
analysis.pure = TOC(
    (name, str(official_ssl) if name == "ssl" else source, kind)
    for name, source, kind in analysis.pure
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
# Replace only the automatically collected libffi dependency. The original
# binary's exact upstream build is unproven; this isolated DLL was built from
# CPython's hash-verified libffi 3.4.4 source and must not silently drift.
rebuilt_libffi = root.parent / "libffi-build" / ".libs" / "libffi-8.dll"
approved_source_build_hash = "17ACEDB4D86F630438BBA70F349F982A9A387C068C8F0D72F2DD1E73B2C92C49"
if not rebuilt_libffi.is_file() or sha256(rebuilt_libffi.read_bytes()).hexdigest().upper() != approved_source_build_hash:
    raise RuntimeError("The isolated libffi source-build bytes are missing or changed")
libffi_entries = [entry for entry in analysis.binaries if Path(entry[0]).name.lower() == "libffi-8.dll"]
if len(libffi_entries) != 1:
    raise RuntimeError("Expected exactly one collected libffi DLL")
analysis.binaries = TOC(
    (dest, str(rebuilt_libffi) if Path(dest).name.lower() == "libffi-8.dll" else source, kind)
    for dest, source, kind in analysis.binaries
)
# The legacy CPython runtime supplies OpenSSL 3.5.8. Replace its two shared
# libraries together with the hash-pinned 3.5.9 build from verified upstream
# source. Never mix one old DLL with one new DLL or silently accept drift.
openssl_build = root.parent / "openssl-sanitized-build" / "openssl-3.5.9"
openssl_dlls = {
    "libcrypto-3-x64.dll": "44A1C07CE88EB12A77FBCB38050B63A80BC61FF4EFB55758136D1A3C7AA8E2BB",
    "libssl-3-x64.dll": "EBF246E9A5FD0FA7B4B2DA9FCD838AF7B057AC133461DDED035BD18379426F23",
}
for name, expected_hash in openssl_dlls.items():
    candidate = openssl_build / name
    if not candidate.is_file() or sha256(candidate.read_bytes()).hexdigest().upper() != expected_hash:
        raise RuntimeError(f"Verified OpenSSL 3.5.9 build is missing or changed: {name}")
    matches = [entry for entry in analysis.binaries if Path(entry[0]).name.lower() == name]
    if len(matches) != 1:
        raise RuntimeError(f"Expected exactly one collected OpenSSL DLL: {name}")
analysis.binaries = TOC(
    (dest, str(openssl_build / Path(dest).name)
     if Path(dest).name.lower() in openssl_dlls else source, kind)
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
