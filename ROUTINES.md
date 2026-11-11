# Stage 6B: explicit-start routines

Routines are owner-managed, named sequences started explicitly from the server's Routines page or by asking Ghost to start a saved routine. Saving or editing a routine never runs it. This stage adds no schedules, location triggers, or automatic startup execution.

## Setup

Deploy the updated server and restart it normally. Sign in to the owner admin page, open Routines, choose a template or build a sequence, and save. Choose the paired source device before starting from the page. An optional assignment limits a definition to one source. Existing phone, tablet, and desktop clients use the server tool; start a new conversation so an already-running session receives the new tool declaration. No client build is included here.

Supported steps are briefing, weather, timer, completion reply, and exact Home Assistant controls for lights, fans, and media players (on/off/toggle). Load available controls in the editor. There are at most eight steps. Completion replies are returned to the initiating source, not broadcast. Timers use the existing household timer system. There are no arbitrary commands, nested routines, scripts, scenes, locks, or alarm controls.

## Access and approval

The initiating device must remain active, approved, and allowed to use routines and each underlying tool. Assignments identify paired device credentials, not the person speaking or a biometric identity. Briefings can contain private calendar information; use an appropriately trusted source.

Home steps use the existing Stage 5 home-service authorization with the exact service plan. When access enforcement is enabled, restricted sources are denied and owner approval can pause a step. Approve through the existing approval centre, then explicitly resume the routine. With access enforcement off, existing legacy authority remains in effect; enabling routines does not itself enable approval enforcement. An allowed home service can still trigger automations configured in Home Assistant.

## Progress and interruptions

Each run keeps its saved definition version, source, step checkpoint, and results in the normal server database. Completed steps are not repeated on approval resume. Editing or disabling the definition prevents continuing an older run. One unfinished run is allowed per source, with a global limit of 32. Repeated starts of the same routine within five seconds return the existing run.

Only a run waiting for approval can resume. A restart marks running work interrupted without automatically replaying it. Failed or interrupted work requires review and cancellation before an explicit fresh start. Review timers and home state first: a failure or restart can leave an action's outcome uncertain.

Cancel stops remaining steps; a step already in progress can finish. Cancellation does not undo home changes or remove timers already created. Deleting a routine also cancels its remaining work. Unfinished runs are preserved. Finished history is pruned on new starts to the latest 100 entries and seven days; normal database backup procedures apply.

Completion delivery to the originating source is best effort. Progress remains on the admin page when that device is offline; reports are not queued for later replay.

## Validation limits

Automated tests cover persistence, duplicate starts, source permissions, exact home matching, approval checkpoint/resume, cancellation, restart recovery, uncertain failures, admin authentication/CSRF, and editor draft preservation and escaping. Home services and provider responses are mocked. No physical home actions were sent. The editor's scripts were checked with a simulated DOM; interactive browser layout and deployment on the NAS, Windows desktop, phone, and tablet still need local verification.
