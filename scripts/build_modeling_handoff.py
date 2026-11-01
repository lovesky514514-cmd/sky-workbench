"""Package reviewable source and replay tools without machine state or solvers."""

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import zipfile


ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOTS = (
    "backend/app/",
    "backend/tests/",
    "frontend/src/",
    "frontend/public/",
    "frontend/tests/",
    "frontend/src-tauri/src/",
    "frontend/src-tauri/capabilities/",
    "frontend/src-tauri/icons/",
    "frontend/src-tauri/resources/java_bridge/",
    "frontend/src-tauri/resources/semantic_runtime/",
    "frontend/src-tauri/resources/licenses/",
    "frontend/src-tauri/resources/public_knowledge/",
    "java_bridge/",
    "knowledge/public/",
    "vnext/",
    "scripts/",
    "docs/",
)
ROOT_FILES = {
    ".gitattributes",
    ".gitignore",
    "README.md",
    "BUILDING.md",
    "LICENSE",
    "RELEASE_NOTES_3.1.0.md",
    "backend/desktop_runtime.py",
    "backend/mathics_worker.py",
    "backend/pytest.ini",
    "backend/requirements.txt",
    "backend/requirements-comsol.txt",
    "backend/requirements-dev.txt",
    "frontend/package.json",
    "frontend/package-lock.json",
    "frontend/index.html",
    "frontend/pet.html",
    "frontend/tsconfig.app.json",
    "frontend/tsconfig.json",
    "frontend/tsconfig.node.json",
    "frontend/vite.config.ts",
    "frontend/src-tauri/Cargo.toml",
    "frontend/src-tauri/build.rs",
    "frontend/src-tauri/Cargo.lock",
    "frontend/src-tauri/tauri.conf.json",
    "frontend/src-tauri/resources/knowledge_base/.gitkeep",
    "frontend/src-tauri/resources/knowledge_base/build_profile.json",
    "frontend/src-tauri/resources/java_bridge/.gitkeep",
    "frontend/src-tauri/resources/semantic_runtime/.gitkeep",
    "packaging/scientific-runtime.spec",
    "packaging/native-inputs.json",
    "packaging/requirements-win-py312.lock",
    "packaging/lab_defaults.nsh",
    "packaging/provider_defaults.nsh",
    "packaging/tls/codex-rejected-cas.pem",
}
SUFFIXES = {
    ".py",
    ".java",
    ".ts",
    ".tsx",
    ".mjs",
    ".cjs",
    ".css",
    ".html",
    ".rs",
    ".json",
    ".md",
    ".txt",
    ".toml",
    ".ini",
    ".ps1",
    ".svg",
    ".lock",
    ".png",
    ".ico",
    ".icns",
}


def build(output):
    output = output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    names = subprocess.check_output(
        [
            "git",
            "-C",
            str(ROOT),
            "ls-files",
            "--cached",
            "--others",
            "--exclude-standard",
        ],
        text=True,
        encoding="utf-8",
    ).splitlines()
    records = {}
    archive = output / "SKY_3.1_Modeling_Handoff_Source.zip"
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
        # Explicit build inputs may live under an ignored packaging directory.
        # Include only the reviewed whitelist, never the whole ignored tree.
        for name in sorted(set(names) | ROOT_FILES):
            path = ROOT / name
            if name not in ROOT_FILES and not (
                name.startswith(SOURCE_ROOTS)
                and (
                    path.suffix in SUFFIXES
                    or name.startswith("frontend/src-tauri/resources/licenses/")
                )
            ):
                continue
            if (
                not path.is_file()
                or path.is_symlink()
                or any(
                    p in {"__pycache__", "node_modules", "target", ".venv", "workspace"}
                    for p in path.parts
                )
            ):
                continue
            data = path.read_bytes()
            records[name] = hashlib.sha256(data).hexdigest()
            bundle.writestr(name, data)
        bundle.writestr(
            "HANDOFF_FILES.json", json.dumps(records, ensure_ascii=False, indent=2)
        )
    # Verify the actual archive rather than assuming the write succeeded.
    with zipfile.ZipFile(archive) as bundle:
        assert bundle.testzip() is None
        assert all(
            hashlib.sha256(bundle.read(name)).hexdigest() == digest
            for name, digest in records.items()
        )
    manifest = dict(
        kind="SOURCE_AND_REPLAY_TOOLS_NOT_AN_INSTALLER",
        archive=archive.name,
        archive_sha256=hashlib.sha256(archive.read_bytes()).hexdigest(),
        bytes=archive.stat().st_size,
        source_files=len(records),
        git_base=subprocess.check_output(
            ["git", "-C", str(ROOT), "rev-parse", "HEAD"], text=True
        ).strip(),
        includes_uncommitted_changes=True,
        excluded=[
            "credentials",
            "API sessions and approvals",
            "private projects and knowledge",
            "connections and absolute executable paths",
            "solvers and licenses",
            "virtualenvs and model weights",
            "built installers",
        ],
    )
    (output / "MANIFEST.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(manifest, ensure_ascii=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    build(parser.parse_args().out)
