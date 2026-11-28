# Stage 7B — manually activated break-in mode

This prototype adds guarded phone activation, selected Home Assistant lights and sirens, and alerts to selected Ghost phones. It does not detect intruders, call emergency services, or provide two-way calling. The existing 000 button opens the phone dialler for a person to place the call. Later stages remain separate.

## Setup and first test

1. Update the server, then install the matching phone update. Use the existing HTTPS and certificate-pinning options before live use.
2. On every participating approved personal phone, unlock Ghost, open Help, then Break-in mode once to register support.
3. In the server owner dashboard, open Help → Stage 7B. Select controller phones and recipient phones. Each controller needs another recipient. Restricted profiles, unapproved phones, tablets and desktop agents cannot participate.
4. Optionally enter exact Home Assistant light.* and siren.* entity IDs, a 30–600 second duration, and a saved home address. No wildcard, lock, alarm-panel or script targets are supported. Empty target lists allow family alerts only.
5. Enable the feature while leaving Force TEST on. On a controller phone, review the exact plan and explicitly confirm it.

TEST sends real, clearly marked alerts to selected family phones, but makes **zero Home Assistant requests**. Verify notification delivery, opening/unlocking, receipt status, explicit acknowledgement and Stop on real phones. A device receipt does not mean a person has responded.

## Live operation

Use Check targets in the owner dashboard before enabling live operation. Live sirens must report turn-on, turn-off and duration support (supported_features includes 19). Home Assistant documents these capabilities at [Siren actions](https://www.home-assistant.io/integrations/siren/) and [Siren entity features](https://developers.home-assistant.io/docs/core/entity/siren/). Reported support is not proof of physical operation: test the exact hardware and its duration cutoff under supervision.

Turning Force TEST off requires owner confirmation. The phone still starts with Test selected and requires a second explicit confirmation of an expiring plan showing exact targets, duration and recipients. Saved-address sharing is optional and unchecked by default; it shares the configured address, not GPS. Only an opted-in address is retained in the request snapshot.

An accepted command means the Home Assistant request was accepted, not that lights or sirens physically operated. Failed and uncertain commands remain visible. Phone alerts depend on server connectivity, notification permission, battery restrictions and Do Not Disturb; they are not guaranteed delivery or dispatch.

Stop silences only sirens that this request attempted to activate; **lights stay on**. Acknowledgement is separate from Stop. Configuration changes close existing requests and attempt to stop their sirens. Unconfirmed stops can be explicitly retried. If the Home Assistant connection changed, Ghost will not send a stop to a different home: use the original Home Assistant or physical controls.

## Recovery and access

- Requests are saved before dispatch. An uncertain phone submission retries the same request ID and plan token, avoiding repeated activation. Discarding a local retry does not stop an actual request.
- Plans expire after two minutes and become invalid if configuration or the Home Assistant connection changes.
- Only one household request may be active at a time. Owner-selected controllers can start/stop; selected recipients can view and acknowledge. Revocation or profile restriction removes access immediately.
- Voice or model navigation can only open the guarded phone screen. It cannot confirm or activate lights, sirens, alerts or calls.
- Restart never replays ON commands or family notifications. Device duration is the first cutoff; while running, the server also attempts Stop at the deadline. Recovery only attempts automatic Stop within 60 seconds after the deadline. Later recovery shows deadline_missed and requires physical/Home Assistant checks, avoiding a late stop of a newly used siren.
- Acknowledgement/access windows expire after one hour. Request history and associated delivery/action data are pruned after 24 hours during subsequent operations. Normal backups may retain older copies.
- Run a single server process: connected-device delivery is process-local.

Automated tests use mocked Home Assistant and phones. No live lights, sirens, family alerts or emergency calls were activated during development. Real phone notifications, screen locks, Home Assistant hardware, sound levels, cutoff behavior and Windows/server deployment still require supervised local validation.

