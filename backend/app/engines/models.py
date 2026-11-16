from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class EngineInput(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class LammpsInput(EngineInput):
    kind: Literal["lammps_lj"] = "lammps_lj"
    unit_system: Literal["lj"] = "lj"
    cells: int = Field(default=3, ge=2, le=8)
    density: float = Field(default=0.8442, gt=0.1, lt=1.5)
    temperature: float = Field(default=1.0, gt=0.05, le=5)
    timestep: float = Field(default=0.005, gt=0, le=0.01)
    steps: int = Field(default=1000, ge=100, le=100000)
    seed: int = Field(default=87287, ge=1, le=900000000)
    equilibration_steps: int = Field(default=1000, ge=0, le=100000)
    thermo_interval: int = Field(default=10, ge=1, le=1000)
    dump_interval: int = Field(default=100, ge=1, le=10000)


class VaspInput(EngineInput):
    kind: Literal["vasp_files"] = "vasp_files"
    unit_system: Literal["vasp_native_eV_angstrom"] = "vasp_native_eV_angstrom"
    files: dict[str, str]

    @field_validator("files")
    @classmethod
    def check_files(cls, files: dict[str, str]) -> dict[str, str]:
        if set(files) != {"INCAR", "POSCAR", "KPOINTS", "POTCAR"}:
            raise ValueError(
                "Supply exactly INCAR, POSCAR, KPOINTS and a licensed local POTCAR"
            )
        if any(
            not value.strip() or len(value.encode()) > 8_000_000
            for value in files.values()
        ):
            raise ValueError("Each input must contain text and be at most 8 MB")
        # VASP may run long jobs; this first adapter supports single local tasks only.
        if any("\x00" in value for value in files.values()):
            raise ValueError("Input must be text")
        from app.engines.vasp import validate_vasp_files

        validate_vasp_files(files)
        return files


class Atom(EngineInput):
    element: str = Field(pattern=r"^[A-Z][a-z]?$")
    x: float
    y: float
    z: float


class VaspWslInput(EngineInput):
    kind: Literal["vasp_wsl"] = "vasp_wsl"
    files: dict[str, str]
    potentials: dict[str, str] = Field(default_factory=dict, max_length=64)

    @model_validator(mode="after")
    def validate_local_references(self):
        import re
        from app.engines.vasp import validate_vasp_files

        if set(self.files) != {"INCAR", "POSCAR", "KPOINTS"}:
            raise ValueError("WSL VASP accepts INCAR, POSCAR and KPOINTS; POTCAR is resolved in Ubuntu")
        if any(not text.strip() or "\x00" in text or len(text.encode()) > 8_000_000 for text in self.files.values()):
            raise ValueError("Invalid VASP input text")
        species = validate_vasp_files(self.files, local_potentials=True)["species"]
        if not self.potentials.keys() <= set(species) or any(
            not re.fullmatch(r"[A-Z][a-z]?(?:_[A-Za-z0-9]+)*", folder)
            for folder in self.potentials.values()
        ):
            raise ValueError("Invalid local potential selection")
        return self


class VaspSiliconDraft(EngineInput):
    lattice_angstrom: float = Field(gt=0, le=20)
    cutoff_ev: float = Field(default=400, gt=0, le=2000)
    mesh: int = Field(default=4, strict=True, ge=1, le=32)

    def files(self) -> dict[str, str]:
        half = self.lattice_angstrom / 2
        return {
            "INCAR": f"SYSTEM = Si diamond primitive\nENCUT = {self.cutoff_ev:g}\nPREC = Accurate\n"
                     "EDIFF = 1E-6\nIBRION = -1\nNSW = 0\n",
            "POSCAR": f"Si diamond primitive\n1.0\n0 {half:g} {half:g}\n{half:g} 0 {half:g}\n"
                      f"{half:g} {half:g} 0\nSi\n2\nDirect\n0 0 0\n0.25 0.25 0.25\n",
            "KPOINTS": f"Gamma mesh\n0\nGamma\n{self.mesh} {self.mesh} {self.mesh}\n0 0 0\n",
        }


class MaterialsStudioInput(EngineInput):
    kind: Literal["ms_forcite_energy"] = "ms_forcite_energy"
    coordinate_unit: Literal["angstrom"] = "angstrom"
    atoms: list[Atom] = Field(min_length=1, max_length=2000)
    forcefield: Literal["COMPASS", "COMPASSII", "Universal"] = "Universal"


TaskInput = Annotated[
    LammpsInput | VaspInput | VaspWslInput | MaterialsStudioInput, Field(discriminator="kind")
]


class EngineTaskRequest(EngineInput):
    title: str = Field(default="Scientific task", min_length=1, max_length=200)
    project_id: str = Field(default="scratch", max_length=100)
    inputs: TaskInput
    timeout_seconds: int = Field(default=600, ge=10, le=86400)
    confirmed: bool = False
    review_ticket: str | None = Field(default=None, max_length=2048)


def engine_for(inputs: TaskInput) -> str:
    return {
        "lammps_lj": "LAMMPS",
        "vasp_files": "VASP",
        "vasp_wsl": "VASP",
        "ms_forcite_energy": "Materials Studio",
    }[inputs.kind]
