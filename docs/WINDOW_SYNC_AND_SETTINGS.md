# Window state and Settings

## COMSOL model state

The model page shows the selected task's Runtime snapshot and verified output images. It does not capture COMSOL's desktop window. The “View live COMSOL model readback” action opens Evidence → Technical inspector → Live COMSOL, where the Java Bridge reads the currently attached model tree and parameters.

Bridge status is global to the local COMSOL attachment. It is separate from the selected task's run and scientific verdict. A tree/parameter pair is displayed only when both responses report successful model readback, match the same model tag, and the Bridge process identity remains unchanged while the requests are in flight. Missing or stale data remains unavailable. Bridge event history may include earlier attachments.

The two HTTP readbacks are not an atomic COMSOL snapshot. A model changing between calls needs a later refresh; this inspector is not a scientific certification path.

Other software tabs provide local script editors and explicit adapter probes. They do not mirror vendor windows or certify scientific results. An older manual screen-picker component remains outside the product navigation; it is not an automatic synchronization path.

## Settings

Open Settings from the header gear, project rail, or More menu. The header gear remains available when the rail is hidden in a narrow layout. The first-run guide can open the AI provider section directly.

General contains local software discovery and COMSOL root configuration. Local models, AI providers, retrieval, extensions, and storage have separate sections. When the Runtime is unavailable or settings have not loaded, the dialog displays the failure and offers logs/restart; it does not show editable stale settings.

## Verification boundary

Frontend unit tests and a production Vite build cover the source paths. This source-only release does not certify a newly built installer or a 6.2/6.4 COMSOL attachment. A real desktop run and each COMSOL version require separate on-device acceptance; this page does not report a new scientific solve.
