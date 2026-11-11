# Stage 6C: requested conversation handoff

An explicit handoff leaves a short recap for another personal device. It does not transfer a live audio connection, remotely start a microphone, copy raw transcript history, migrate running work, or reuse action approvals. The source conversation remains open.

## Enable and use

Deploy the server update and restart normally. In the owner admin page, open People / Personal replies, assign your phone and desktop to the same person, and enable Conversation handoff. It is off by default. Personal assignments identify device credentials, not whoever happens to hold the device; protect those devices accordingly. Shared tablets, room speakers and unassigned devices are excluded even when recognition identifies someone.

Start a new conversation on existing clients to load the new server tool. Ask, for example, “Leave a recap of our holiday planning on my phone.” Ghost lists eligible targets when needed and submits a short recap to the exact selected device. On that device ask “Check my pending conversation handoff,” then ask to accept it. You can also ask to cancel an offer from either endpoint. No new phone or desktop build is included.

The destination receives no unsolicited alert or spoken recap. Its pending list contains only offer identifiers and expiry, not the recap. Acceptance returns the recap into that current conversation through the ordinary server-tool response. Ask Ghost to continue the topic. This also works when the destination was offline when the offer was created, provided you accept within ten minutes. It does not inject context into all future sessions.

## Privacy and limits

Both endpoints must remain approved, active, permitted phones/desktops assigned to the same person. Stage 5 restricted sources are denied when access enforcement is enabled. Those checks run again at acceptance. Face matches, voice matches and spoken names grant no access. The destination's current permissions and approvals still apply to any new actions.

Recaps are model-written, up to 2000 characters. The tool instructs Ghost to omit passwords, verification codes, message bodies and unnecessary sensitive details; this instruction is not a guaranteed sensitive-content detector. Ask for a specific non-sensitive recap and avoid including secrets. Imported recap text is labeled untrusted conversation data and grants no authority. User-request and acceptance consent are handled by the conversation model; this is not a separate authenticated human confirmation screen.

Offers expire after ten minutes and are deleted on the next handoff operation. A new offer replaces the source's prior offer and the destination's prior inbox item. At most 100 offers are retained. Acceptance consumes the offer once; if a network response is lost after acceptance, ask the source to create a fresh recap. No automatic action replay or delivery retry occurs. Turning the setting off clears pending offers. Deleting an offer does not erase copies already in an accepted conversation, provider logs, or database backups.

Pending recaps use the existing local server database and its backup/access protections; they are not end-to-end encrypted. Routines, background tasks and automations cannot invoke this tool. No other device is contacted until the user actively uses the destination conversation.

## Validation

Tests cover same-person eligibility, shared-device exclusion, one-use destination-bound acceptance, expiry, replacement, cancellation, revocation, changed assignments, disabled access, task exclusion, and owner login/CSRF on the opt-in setting. Existing server and admin-script checks are also run. No real model/provider or paired device is contacted by these tests. Live voice delivery, mobile/Windows clients, interactive admin layout and NAS deployment need local verification. Shared-tablet handoff requires a future private-session design and is intentionally excluded here.
