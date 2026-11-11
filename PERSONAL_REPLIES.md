# Stage 6A: personal replies and vocabulary hints

This is the first small part of Stage 6. It builds on Stage 5 household access;
routines and conversation handoff are separate follow-up parts.

After deploying the server update, open **People → Personal replies & vocabulary**.
Existing people are available, or create a person without camera/voice enrolment.
Set a preferred name, short/balanced/detailed replies, reply language and units.
Add up to 30 one-line vocabulary hints, such as `Zoe → Zoey` or
`parramata → Parramatta`. Save the person's form.

In **Personal device assignments**, choose that person for their personal phone or
PC. Leave shared tablets and rooms **Shared / unassigned**. Each device has at most
one explicit assignment; changing an assignment does not change its security role.
The owner makes changes through the existing signed-in admin page, with its CSRF,
two-step login and auto-lock rules. Device keys and spoken names cannot edit or
select these profiles. Stage 5 restricted profiles do not receive personal data,
even when an administrator has configured a personal assignment for that device.

Start a **new conversation** to get updated voice-session preferences. Server text
chat rebuilds its prompt for each request. Already running provider sessions, cached
client prompts and offline mode are not updated or erased by a server preference
change. No companion APK change is needed for the existing server-session path.
Other providers or clients that do not use that session prompt may not apply hints.

Preferences are suggestions for model replies and interpretation, not enforced output
guarantees. Units affect replies, not weather widgets or integration configuration.
Reply language does not change the selected voice, wake-word model or speech engine.
Vocabulary hints do not train acoustic recognition, clone a voice, change the saved
transcript or automatically substitute commands, confirmations, numbers, codes or
message text. Ambiguous meanings should be clarified; existing permission and
message-consent rules take priority. Use names/phrases, not secrets or code contents.

Assignments are not an identity check. Face/voice matches never automatically apply
a different person's private preference record. Shared room/satellite requests never
inherit the parent hub's assignment. An owner can deliberately assign a shared device,
but everyone using it then gets that device's chosen reply style. This is not a
per-person authentication system or a new privacy barrier between Adult/Owner devices.

**Reset preferences** clears the chosen style/vocabulary and keeps the person and
device assignment. Shared/unassigned removes the device binding. Deleting a person
through the existing People page removes their face/voice samples, reply preferences
and all associated bindings. Old conversations and normal database backups are not
erased. Preference JSON and device bindings live in the existing server database;
admin audit messages record the action, not vocabulary/profile contents.

## Validation

- Full server suite: **76 passed, one skipped**, including eight new tests for device
  isolation, unassigned/shared/restricted handling, admin/CSRF checks, bounded input,
  reset/deletion, unchanged access rules, unchanged voice/transcripts and duplicate
  names/revoked assignments.
- Actual admin JavaScript passed syntax, fake-DOM rendering/escaping, vocabulary
  parsing and existing Stage 5 admin checks.
- No real household device, AI-provider conversation or recognition hardware was used.
  Physical phone/tablet/desktop behaviour, interactive browser layout, offline behaviour,
  real model adherence to language/style/hints and NAS deployment remain untested.

Try two people on two personal devices, a shared/unassigned tablet and a restricted
profile. Check a new conversation uses the intended style, vocabulary remains a hint,
and reset/unassignment/deletion remove future prompt personalisation as described.
