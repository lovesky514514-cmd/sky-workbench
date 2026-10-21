import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from app.config import BACKEND_ROOT


def test_python_node_and_worker_arguments_survive_space_paths(tmp_path: Path) -> None:
    root = tmp_path / "COMSOL Workbench Test"
    request_dir = root / "request files"
    workspace = root / "scientific workspace"
    request_dir.mkdir(parents=True)
    request_path = request_dir / "request.json"
    request_path.write_text(
        json.dumps(
            {
                "run_id": "space-path-test",
                "workspace": str(workspace),
                "plan": {
                    "parameters": {
                        "width": {"value": 10, "unit": "inch"},
                        "height": {"value": 5, "unit": "inch"},
                        "left_temperature": {"value": 373, "unit": "K"},
                        "right_temperature": {"value": 293, "unit": "K"},
                        "thermal_conductivity": {"value": 238, "unit": "W/(m*K)"},
                        "density": {"value": 2700, "unit": "kg/m^3"},
                        "heat_capacity": {"value": 900, "unit": "J/(kg*K)"},
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    completed = subprocess.run(
        [sys.executable, str(BACKEND_ROOT / "app" / "comsol" / "heat_worker.py"), "--request", str(request_path)],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    assert completed.returncode != 0
    assert "Unsupported geometry unit: inch" in completed.stderr
    assert (workspace / "models").is_dir()
    assert (workspace / "outputs" / "space-path-test").is_dir()

    node = shutil.which("node")
    if node is None:
        pytest.skip("Node is not installed")
    node_result = subprocess.run(
        [node, "-e", "console.log(process.cwd())"],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=10,
        check=True,
    )
    assert str(root) in node_result.stdout.strip()

