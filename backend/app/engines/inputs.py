from __future__ import annotations

import os
import re
from pathlib import Path

from app.engines.models import LammpsInput, MaterialsStudioInput, TaskInput, VaspInput


def prepare_inputs(inputs: TaskInput) -> dict[str, str]:
    if isinstance(inputs, VaspInput):
        return dict(inputs.files)
    if isinstance(inputs, LammpsInput):
        # Reduced LJ units describe an exploratory fluid, not an identified material.
        return {"in.lammps": f"""units lj
atom_style atomic
boundary p p p
lattice fcc {inputs.density:.12g}
region box block 0 {inputs.cells} 0 {inputs.cells} 0 {inputs.cells}
create_box 1 box
create_atoms 1 box
mass 1 1.0
pair_style lj/cut 2.5
pair_coeff 1 1 1.0 1.0 2.5
neighbor 0.3 bin
neigh_modify delay 0 every 1 check yes
timestep {inputs.timestep:.12g}
thermo {inputs.thermo_interval}
thermo_style custom step temp pe etotal press atoms
minimize 1.0e-6 1.0e-8 100 1000
velocity all create {inputs.temperature:.12g} {inputs.seed} mom yes rot yes dist gaussian
fix thermostat all nvt temp {inputs.temperature:.12g} {inputs.temperature:.12g} 0.5
run {inputs.equilibration_steps}
reset_timestep 0
compute sky_msd all msd com yes
thermo_style custom step temp pe etotal press atoms c_sky_msd[4]
print "SKY_PHASE PRODUCTION"
dump trajectory all custom {inputs.dump_interval} trajectory.dump id type x y z
dump_modify trajectory sort id
run {inputs.steps}
write_data final.data
"""}
    if isinstance(inputs, MaterialsStudioInput):
        atoms = "\n".join(
            f'$doc->CreateAtom("{atom.element}", Point(X => {atom.x:.12g}, Y => {atom.y:.12g}, Z => {atom.z:.12g}));'
            for atom in inputs.atoms
        )
        # Numeric coordinates and an enum force field keep arbitrary Perl out of this path.
        return {"main.pl": f'''use strict;
use MaterialsScript qw(:all);
my $doc = Documents->New("model.xsd");
{atoms}
$doc->CalculateBonds;
Modules->Forcite->Energy->Run($doc, Settings(CurrentForcefield => "{inputs.forcefield}", AssignForcefieldTypes => "Yes"));
$doc->Save;
open(my $fh, '>', 'energy.txt') or die "Cannot write energy";
print $fh $doc->PotentialEnergy;
close($fh);
'''}
    raise ValueError("Unsupported engine input")


def command_for(engine: str, executable: str) -> list[str]:
    if engine == "LAMMPS":
        return [executable, "-in", "in.lammps", "-log", "log.lammps", "-screen", "none"]
    if engine == "VASP":
        return [executable]
    if engine == "Materials Studio" and "runmatscript" in Path(executable).name.lower():
        if Path(executable).suffix.lower() in {".bat", ".cmd"}:
            if os.name != "nt" or re.search(r'[&|<>^%!"\r\n]', executable):
                raise ValueError("MaterialsScript launcher path contains unsupported characters")
            # Only the configured vendor launcher reaches cmd; the script name is fixed.
            return [os.environ.get("ComSpec", "cmd.exe"), "/d", "/s", "/c", "call", executable, "main"]
        return [executable, "main"]
    raise ValueError("Configure the installed RunMatScript launcher, not the Materials Studio desktop")
