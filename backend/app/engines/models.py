from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


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


class MaterialsStudioInput(EngineInput):
    kind: Literal["ms_forcite_energy"] = "ms_forcite_energy"
    coordinate_unit: Literal["angstrom"] = "angstrom"
    atoms: list[Atom] = Field(min_length=1, max_length=2000)
    forcefield: Literal["COMPASS", "COMPASSII", "Universal"] = "Universal"


TaskInput = Annotated[
    LammpsInput | VaspInput | MaterialsStudioInput, Field(discriminator="kind")
]


class EngineTaskRequest(EngineInput):
    title: str = Field(default="Scientific task", min_length=1, max_length=200)
    project_id: str = Field(default="scratch", max_length=100)
    inputs: TaskInput
    timeout_seconds: int = Field(default=600, ge=10, le=86400)
    confirmed: bool = False


def engine_for(inputs: TaskInput) -> str:
    return {
        "lammps_lj": "LAMMPS",
        "vasp_files": "VASP",
        "ms_forcite_energy": "Materials Studio",
    }[inputs.kind]
