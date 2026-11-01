"""Generate only SKY-authored reference notes, never vendor manual extracts."""

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
NOTES = [
    ("COMSOL", "bridge", "COMSOL connection and validation", "COMSOL 连接成功、模型树读回和科学验证分别记录。Connection, model readback and scientific validation require separate evidence for each run.", "https://github.com/MPh-py/MPh"),
    ("MATLAB", "pde", "MATLAB PDE Toolbox thermal boundary", "MATLAB 的传热有限元任务需要 PDE Toolbox。热边界应按几何位置核对，并与解析解比较。Thermal boundary selection and analytical temperature checks are independent of process exit.", "https://www.mathworks.com/help/pde/"),
    ("VASP", "scf", "VASP electronic and ionic convergence", "VASP 电子自洽收敛与离子优化收敛是不同判据。NSW 为零的单点计算无需离子优化收敛，但仍需电子自洽证据。Electronic convergence and ionic convergence are separate checks.", "https://vasp.at/wiki/index.php/NSW"),
    ("Materials Studio", "modules", "Materials Studio module selection", "Materials Studio 的 Forcite、CASTEP 和 DMol3 属于不同计算模块。模块、输入模型、力场或电子结构设置应分别确认。Forcite molecular simulation and CASTEP or DMol3 electronic structure require separate module contracts.", "https://www.3ds.com/products/biovia/materials-studio"),
    ("LAMMPS", "trajectory", "LAMMPS compute msd and diffusion", "LAMMPS compute msd 使用包含周期穿越的位移计算均方位移。MSD 对时间的线性拟合需要先判断扩散区间。Mean squared displacement uses unwrapped coordinates; a linear fit alone does not validate a diffusion coefficient.", "https://docs.lammps.org/compute_msd.html"),
    ("LAMMPS", "outputs", "LAMMPS thermo output completion", "LAMMPS thermo 的输出频率不等于采样充分性。运行开始和结束也会输出热力学记录，完成检查应核对请求的最终步数。Thermodynamic output and completed timesteps do not establish equilibration.", "https://docs.lammps.org/thermo.html"),
    ("Mathics3", "symbolic", "Mathics3 symbolic calculation", "Mathics3 可用于符号表达式计算；运算返回值、数值检查与科学结论分别记录。Mathics3 symbolic calculation does not imply complete Mathematica compatibility.", "https://github.com/Mathics3/mathics-core"),
    ("shared", "evidence", "Scientific assumptions and source evidence", "来源可定位、参数使用授权和科学验证是不同状态。缺少参数时可提出带适用范围的候选假设；假设不会因此成为已验证事实。Source location, assumption authorization and scientific validation remain distinct.", "https://github.com/lovesky514514-cmd/sky-workbench"),
]


def main():
    folder = ROOT / "knowledge" / "public"
    folder.mkdir(parents=True, exist_ok=True)
    units = []
    for index, (engine, module, title, content, url) in enumerate(NOTES, 1):
        units.append({"unit_id": f"SKY-PUBLIC-{index:03d}", "title": title, "content": content,
                      "metadata": {"engine": engine, "module": module, "version_independent": True,
                                   "license_class": "public_redistributable", "source_url": url,
                                   "source_revision": "SKY-3.1.0-authored-summary",
                                   "verification_status": "UNVERIFIED", "project_id": None}})
    data = (json.dumps(units, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    (folder / "units.json").write_bytes(data)
    manifest = {"schema_version": 1, "version": "3.1.0", "units": len(units),
                "license": "Apache-2.0", "content_origin": "SKY-authored reference notes; external links are references only",
                "vendor_manuals_included": False, "files": {"units.json": hashlib.sha256(data).hexdigest()}}
    (folder / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Generated {len(units)} public reference notes")


if __name__ == '__main__':
    main()
