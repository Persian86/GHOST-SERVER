# Stage 5: household access and action approval

Install this server update, then open the admin page → **Security → Household
access**. No companion APK update is required: their existing Privacy Centre links
already open this dashboard. Enforcement is **off by default**, preserving existing
installations. Assign your personal phone/PC **Owner**, review other device profiles,
then enable enforcement. New devices paired while enabled start as Guest.

Profiles are a **device-wide ceiling**, not authenticated individual logins. Everyone
using a shared tablet gets that tablet's permissions. Recognised voices/faces, a
spoken name, renamed device, or a model's `confirmed=true` cannot elevate a profile.

| Profile | Server access when enforcement is on |
|---|---|
| Owner | Household features; eligible to approve waiting devices from an existing owner-phone/PC notification. Sensitive actions still need signed-in admin approval. |
| Adult | Household features, with the same sensitive-action approval requirement; cannot approve waiting devices from device credentials. |
| Child / Guest | General conversation, weather, news and reasoning. No new household memories/history, calendars, notes, files/photos, cameras, recognition profiles, device lists or home controls. This is a feature-access ceiling, not a child-content filter. |

Restricted sessions receive a minimal prompt, filtered server tools, no old server
chat history, no owner name and no raw/away-mode API key. Voice requires a short-lived
token. HTTP routes and tool execution are checked server-side, including nested tasks
and room-speaker requests. Household WebSocket snapshots/pushes are blocked for these
profiles, except trusted server-generated safety alerts and connection control messages.

## Owner action approval

While enabled, generic remote device actions (including server-chat calls to local
device tools), Home Assistant changes, scene/script execution, arbitrary Home
Assistant assistant requests and home-tile writes create an approval request instead
of acting. Fixed-purpose existing features such as find-phone ringing and camera
viewing retain their Adult/Owner access; they are not generic remote control.

On **Security → Actions awaiting owner approval**, review the device, operation and
exact arguments, then choose **Approve once** or **Decline**. The admin browser's
existing password, optional two-step login, session, CSRF check and auto-lock apply.
Device bearer keys cannot approve actions or assign roles.

Approval does not execute the action. A subsequent identical retry from the source
device may execute it once within five minutes of the original request. Approval is
bound to device ID, configured profile, operation and canonical arguments; resolved
remote-device/entity IDs are bound before dispatch. Changing an alias/argument cannot
reuse it. Concurrent retries cannot consume the same approval twice. Declines remain
effective until expiry. Role/mode changes invalidate approvals and existing camera
view links. Device removal blocks new tool calls; active sockets close on role changes
or revocation. WebSocket result replies must come from their actual target device.

Existing administrator-authored internal server work without a device identity is
trusted. Do not use this mode as a replacement for reviewing server automations,
Home Assistant permissions or physical device access.

## Boundaries and retention

This server feature does not revoke operating-system permissions or stop tools an
app runs locally without consulting the server. It cannot erase old client transcripts,
downloaded files, cached prompts, previously shared away-mode keys, already issued
provider tokens or already streaming images. Set up and clear a device before handing
it to a child/guest; changing a profile does not turn an already provisioned personal
phone into a fully isolated guest appliance. New server requests are constrained.

Approval previews are stored as plain JSON in the existing server SQLite database,
visible only through authenticated admin routes. Requests are valid for five minutes;
expired rows are pruned on the next approval request/list/review. Approval decisions
are logged by request ID, without copying arguments into the security log. Normal
database backup retention applies. Turning enforcement off restores legacy server
access without deleting the configured profiles.

## Validation and rollout

The server regression suite and added policy/HTTP/WebSocket tests cover restricted
data/tool access, credential suppression, prompt isolation, owner/role assignment,
exact one-use approvals, expiry/decline/revocation, concurrent retries, target-alias
changes, home-service/tile checks, CSRF/admin authentication, file/photo routes,
safety-alert allowance, socket closure and target-bound result replies.
The real admin JavaScript passes syntax and fake-DOM rendering/escaping checks.
Final suite: 68 passed, one skipped, including 15 new access-policy tests.

No household devices or Home Assistant actions were used in tests. Physical companion
behaviour, browser layout/interaction, real NAS deployment and live home integrations
remain untested. Start with one test device, confirm basic conversation and expected
denials, review a harmless remote action, and verify expiry/decline before using this
for locks or other consequential controls. This stage adds no emergency detection or
automatic emergency-contact messages.
