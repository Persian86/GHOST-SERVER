# Stage 7C — one-time missed check-ins

A person schedules a timer on their own approved Ghost phone. At its due time the server attempts a private reminder to that phone. If the participant does not tap **I’m okay** before the grace period ends, the server attempts a missed-check-in alert to the owner-selected family phones. This is a family notification prototype, not monitored emergency dispatch. A missed check-in does not establish that an emergency occurred.

## Setup and TEST

1. Update Ghost Server first, then install the matching Android phone build. Use the existing HTTPS and certificate-pinning configuration for private communications.
2. Open Help → Check-ins on every participating updated phone and unlock Ghost to register support.
3. In the owner dashboard, open Help → Missed check-ins — Stage 7C. Choose which approved personal phones can schedule their own timers, and which receive missed-check-in alerts. Every participant needs at least one other recipient. The feature is disabled initially and Force TEST is initially on.
4. Enable it with Force TEST still on. On a participant phone, schedule a TEST in one minute with a one-minute grace period. The confirmation names all recipients, who can view the schedule and its status from that point.
5. Verify the reminder on the participant phone. First test I’m okay, then schedule a second TEST and deliberately let its grace period expire. Verify the marked TEST family alert, phone receipt, explicit family acknowledgement, and the participant’s subsequent I’m okay update. TEST uses real phone notifications.
6. Confirm the phone locks and notification settings behave as intended. Repeat with the app in the background and with each phone disconnected, so delivery limitations are understood. Supervise these tests and tell recipients beforehand.

Live timers allow 5–1440 minutes; TEST allows 1–1440. Grace periods allow 1–60 minutes. Timers are one-time and do not recur. Default phone values are 30 minutes plus 5 minutes grace; Test is initially selected even when the owner allows live timers.

## Controls and privacy

- Only the participant schedules and finishes their own timer. Family can acknowledge seeing a missed check-in; that does not check the participant in, stop their timer, confirm a response, or mean help is on the way.
- I’m okay and Cancel require visible confirmation after the existing app unlock. Owner cancellation or saving setup cancels active timers and records that reason, never records the person as safe.
- No new location collection or sharing, no SMS, alarms, Home Assistant commands, automatic calls or 000 calls are added. Two-way calling is a later stage.
- Model/voice navigation can open the guarded Check-ins screen; it cannot schedule, finish, cancel or acknowledge timers.
- Notification content is generic and private on the lock screen. Details remain inside the guarded app. Notices do not speak names or message contents. Existing device access restrictions, approval, revocation and HTTPS settings apply.
- A saved timer runs on the server even if the app is closed. Closing the screen or discarding a local retry does not cancel it. Use its Cancel control and verify the result.
- Removing a participant, revoking access, disabling the feature or saving setup cancels affected timers. Removed or restricted phones immediately lose access. Already transmitted notifications cannot be recalled.
- A successful send to a connection is not a device receipt. A device receipt is not human acknowledgement. The screens keep these separate. If a send times out or a phone is offline, delivery remains unconfirmed and is not automatically retried.

## Timing and recovery limits

The phone shows the server’s saved due and grace deadlines in the phone’s local time. The server checks roughly every two seconds while available; these are not exact device alarms. Keep the server clock accurate and the server running as **one process** with its background tasks enabled.

New timers require a recent healthy scheduler heartbeat. The dashboard and phone report a stale or unavailable heartbeat. A healthy heartbeat does not prove phone reachability or notification delivery.

Confirmations are bound to the participant and current owner setup and expire after two minutes. Uncertain submissions persist the same request body locally. Retrying returns the original saved timer and deadline; it never creates a duplicate while that record remains. An expired confirmation cannot create a fresh timer after history is pruned. If no record was saved, discard the expired local retry and review a new timer.

On restart, pending notifications may resume only while relevant. A reminder is sent only during its grace period. If recovery happens after grace but within five minutes, the missed alert may still be sent. If recovery is later, the request shows **alert window missed** and no stale family alert is sent. Claimed or uncertain sends are never replayed. A notification already in transmission when cancellation happens may arrive; opening it retrieves the current state.

Each timer expires one hour after its escalation deadline. Data is pruned during subsequent operations 24 hours after that expiry (at most about 50 hours after creation for a 24-hour timer). Normal backups may retain older copies. Only one active timer per participant is allowed; starts are limited to 20 per hour.

Phone notifications depend on permission, connectivity, Android battery restrictions and Do Not Disturb. A phone without a server connection cannot report I’m okay; family may still receive a missed alert. Review the server status and contact family directly when appropriate.

## Validation and rollout

Automated tests mock phone delivery. They cover deadlines, grace periods, explicit check-ins, role/privacy checks, retries, stale confirmations, cancellation races, restart recovery, unavailable schedulers and delivery uncertainty. Admin behavior checks and the full Android CI build are included.

Real phone notification delivery, locks, background restrictions, connectivity, the deployed server process and household use remain untested until supervised local validation. No real family notifications, household actions or emergency calls were triggered during development. This update is separate from existing Stage 7A Help and Stage 7B Break-in mode.

