# SKY Workbench 3.0 — COMSOL integration scope

SKY Workbench is an independent, local-first **scientific workflow assistant**.
Its core purpose is to help a user of separately licensed COMSOL software
plan, execute and check a restricted set of supported scientific workflows.
It is **not** a replacement for COMSOL, not a generic multiphysics solver,
and not affiliated with or endorsed by COMSOL AB.

The release must enforce the declared scope in code, not only through README:

1. All distributed modeling and solve APIs pass through registered supported
   ScientificPlan tools, local authentication, and mandatory readback/
   evidence-based scientific validation.
2. No arbitrary model/physics/solver execution path accessible to an end user
   may bypass these gates. Generic internal helper code may remain in source
   only if externally callable routes and tool registration are constrained.
3. COMSOL and its license/JAR files/models are not distributed. A user must
   install a compatible licensed COMSOL product separately, and comply with
   its license terms. Missing COMSOL must degrade safely.
4. Source license: Apache-2.0 only for owner-controlled first-party code.
   Asset terms: see `ASSET_LICENSE.md`. Dependencies retain their own licenses.

Legal boundary: COMSOL 6.3 SLA §1(d), §3(b)(ii), §3(b)(iv)(A)-(D):
https://cdn.comsol.com/doc/6.3.0.638/COMSOL_SoftwareLicenseAgreement.pdf

§3(b)(ii) gives conditional distribution rights; it is not unconditional.
§3(b)(iv)(A) restricts general-purpose multiphysics/competitive applications.
§3(b)(iv)(B) restricts certain license terms and software combinations; MPL
and other components embedded in the candidate need *specific* compatibility
review. Apache-2.0 being unlisted is not proof the combination complies.

This file documents intended scope and required verification, not an external
legal opinion or automatic permission to publish.
