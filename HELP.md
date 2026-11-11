# Stage 7A: manual Help and family-device acknowledgement

This is the first emergency feature, not a monitored emergency response service. It sends manual Help requests between configured Ghost Android phones. It does not call anyone, send SMS, trigger lights/sirens, start check-ins, detect intruders, or notify emergency services. Voice activation and two-way calling are later stages. Existing sensor alerts are unchanged.

## Install and configure

1. Deploy this server update and restart normally, then install the companion Stage 7A mobile update on the initiating and receiving phones. No desktop/tablet update is part of this stage.
2. Pair/approve each phone. On each updated phone, open Home → Help & family alerts once. This registers support with the server.
3. Sign into the owner admin page → Help. Select phones allowed to request help and phones that receive requests. Only approved, unrestricted phones with registered Help support can be selected. These are paired device credentials, not proof of who holds a phone. No address-book contacts or unpaired relatives are supported yet.
4. Optionally enter a saved home address. Enable Help and leave Test mode on. Both Help-off and Test-on are defaults.
5. Send a clearly labelled TEST request from one phone to another. Test alerts really reach selected recipients. Verify notification permission/channel settings, receipt, location visibility and explicit acknowledgement on every recipient before considering live mode.
6. After testing, the owner can turn off Test mode with a separate confirmation. Phone requests still require a visible confirmation; the server forces TEST whenever owner Test mode is on.

Removing a phone from either configuration list removes the corresponding access to request details. Restricted Child/Guest devices are excluded when Stage 5 access enforcement is enabled. Face recognition, spoken names and model-generated confirmations do not authorize requests. No LLM tool is registered for Help.

## Phone flow and location

The Home tab has a Help & family alerts button. The Help screen includes a separate, confirmed shortcut to open Australia's 000 dialler. It does not place the call or speak to the operator. The shortcut needs no Ghost server connection; actual calling still depends on the phone and network. It is labelled Australia rather than silently inferring a local emergency number when travelling.

Location sharing is unchecked by default. If explicitly selected and Android permission is granted, this first build shares an available cached phone fix less than 24 hours old, labelled LAST KNOWN with its timestamp and reported accuracy. It does not request a fresh GPS fix or continuously track the phone. No suitable fix or denied permission means location unavailable. The final confirmation states which location would be shared. Device-reported coordinates are not independently verified.

The separate “I am at the saved home address” option shares the owner-saved address, labelled as selected by the user and not GPS. It is mutually exclusive with phone coordinates. A phone's missing location never silently becomes the home address. Recipients can open a map at the labelled last-known coordinates; no address reverse-geocoding service is called by Ghost.

Notifications use generic text without the source name, address or coordinates and do not speak the alert. Detailed requests are shown inside the existing app-lock flow, and screenshots are disabled on that screen. Existing paired connection/TLS settings apply; use the server's HTTPS setup for private location data. Recaps, raw chat history and microphone audio are not included.

## States, failure and cancellation

- **Saved / pending:** the server has a record and selected recipient snapshots, not proof of delivery.
- **Sent to connection:** the server wrote a notification to an agent connection; phone receipt remains unconfirmed.
- **Phone received:** the recipient app fetched the request and posted a receipt. This does not prove the notification was visible, audible, or seen by a person.
- **Person acknowledged:** someone explicitly pressed “I have seen this request” and confirmed. This does not mean someone is responding or emergency services were contacted.
- **Delivery unconfirmed:** the phone may be offline or sending failed. No automatic SMS/call fallback is present.

Refresh on the initiating phone to see current receipts/acknowledgements. The receiving app posts a device receipt when it handles a valid notification or renders a request. Android notification settings, Do Not Disturb, battery restrictions and connectivity can prevent an alert; blocked notification settings are shown on the Help screen. No Do Not Disturb bypass is promised.

A confirmed send stores an idempotency key and exact pending payload in private app storage until success, or expiry cleanup on next use. If the response is lost, the UI reports uncertainty; retry uses those same details and does not issue a second alert. Only one unexpired, uncancelled request per initiating phone is allowed. Refresh never resends. After a server restart, persisted records remain available, but sends are not automatically replayed. An interrupted send can remain pending/unconfirmed.

Requests expire after one hour without escalation. Only the initiating phone can cancel, with confirmation. Cancellation records the state and makes a best-effort generic cancellation notification; it cannot undo an alert already seen. Offline recipients can manually open Help to review requests later, including cancellation/expiry. No unsolicited delayed replay occurs. Turning Help off prevents new requests; existing configured participants can still review or cancel active ones. Removing their assignment blocks access immediately.

History/location is retained for up to 24 hours and deleted on the next Help operation after that period, including associated delivery rows. Backups may retain prior copies according to the operator's backup policy. Unknown request IDs and access checks do not expose unrelated incidents. The admin Help page can see household request history. Use the normal single-process Ghost Server deployment; the live agent hub is process-local.

## Validation and rollout limits

Server tests simulate recipients and transport. They cover defaults, opt-in, source/recipient binding, duplicate submissions, test/live switching, location validation, stale coordinates, explicit saved addresses, cancellation, expiry, retention, revocation, policy changes, offline transport, authentication and CSRF. Admin-script checks cover escaped location/device text, delivery wording and preservation of unsaved setup edits. No physical alerts, calls or home actions are sent.

The companion mobile code is compile-checked against Android 35 using the real ServerApi and new Help classes, with stand-ins for app-specific lock/TLS/key storage and FragmentActivity. This is not a full Gradle/APK build or device runtime test. Test actual app-lock transitions, location/notification permissions, Maps, dialler handoff, background delivery, offline/cancel behaviour and screen layout on installed phones before enabling live requests. Server deployment on the NAS remains untested here.

The remaining order is Stage 7B break-in mode, Stage 7C missed check-ins, Stage 7D two-way calling prototype with consenting test contacts, Stage 7E family calling escalation, and Stage 7F emergency-service feasibility. No live 000 tests are part of this work.
