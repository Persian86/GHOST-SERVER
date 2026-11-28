# Stage 7D — Ghost speaks and listens to a consenting telephone test contact

This prototype makes a manually confirmed outbound telephone test using Twilio. Ghost introduces itself as an AI and waits for the contact to press **1** before speech processing starts. It then uses turn-by-turn speech recognition and short replies from the server’s existing Anthropic model. Telephone speech uses **Polly.Olivia-Neural (Australian English)** through Twilio; it is not Ghost’s usual Gemini Live voice or a full-duplex streaming conversation.

This is separate from emergency dispatch, emergency-number dialling and automatic family escalation. It does not route to emergency services, connect an operator, make follow-up calls, send SMS, share location or operate household devices. Existing Stage 7A–7C behavior is unchanged.

## Prerequisites

For DRY RUN, no telephony or AI account is needed. For a real supervised test:

- A Twilio account with a voice-capable caller number authorized for outbound calls to the chosen ordinary Australian landline/mobile. Trial accounts may require verified destination numbers; check the account’s own permissions and billing.
- The server’s existing Anthropic API key and fast-model setting. This prototype does not use the tool-enabled household chat path.
- A public HTTPS origin on port 443 with a publicly trusted certificate. The existing private/self-signed phone pin is not a substitute for a publicly trusted callback certificate. Twilio documents its [HTTPS and signature requirements](https://www.twilio.com/docs/usage/security).
- Reverse-proxy routing for POST /api/v1/testcalls/provider/* to this server. Keep the public origin exact; no path prefix, query, credentials or IP literal is accepted. Forward the original scheme only through trusted proxy configuration so real phone confirmations are recognized as HTTPS. Exposing the provider callback path does not require exposing the owner dashboard.
- The matching Android update and an approved, unrestricted personal phone. Desktop/tablet agents and restricted profiles cannot initiate these tests.

Set these values in the **server process/container environment**, outside source control:

| Variable | Value |
| --- | --- |
| TWILIO_ACCOUNT_SID | Your Twilio account SID |
| TWILIO_AUTH_TOKEN | That account’s Auth Token, used for API requests and webhook signature validation |
| TWILIO_FROM_NUMBER | Your authorized caller number in international + format |
| GHOST_CALL_PUBLIC_URL | The exact origin, for example https://ghost.example.com |

Restart after changing the environment. Do not paste credentials into chats or commit them. The dashboard reports only whether required settings are present/valid; this is not a connectivity, credit, number-ownership or provider-permission test.

The server adds the official Twilio Python SDK to requirements.txt. No Android telephony, microphone, recording or call permissions are added; the phone is only a guarded remote control for the server-side test.

## Setup and DRY RUN

1. Update the server first, then install the matching phone build.
2. Open Help → Telephone test calls once on the approved phone, and unlock Ghost, to register support.
3. In the owner dashboard, open Help → Telephone conversation tests — Stage 7D.
4. Select allowed initiating phones. Add up to five test contacts, each with a name, unique ordinary Australian +61 landline/mobile number and explicit prior test permission. Confirm that permission with the person before adding them.
5. Enable telephone tests while leaving **Force DRY RUN** on. The feature is disabled and Force DRY RUN is on by default.
6. On the phone select a contact, review the exact name/number, and confirm DRY RUN. This writes a simulated test record and makes **zero provider or AI requests**.

Only ordinary +61 numbers beginning with Australian geographic/mobile prefixes 2, 3, 4, 7 or 8 are accepted, with eight following digits. Short codes, service numbers, emergency numbers, extensions, SIP addresses and overseas destinations are rejected. A selected number cannot equal the Twilio caller number. No free-form destination can be supplied from the phone.

Owner-selected initiating phones can see the approved contact names/numbers and their own test history. Other phones cannot see or stop those calls. The owner can review and end all test calls. Contact numbers are stored in the server database; use the existing private-access and HTTPS protections.

## Arrange a supervised real test

Configure the prerequisites, then use HTTPS to turn Force DRY RUN off in the owner dashboard. This requires a separate confirmation. The phone still initially selects DRY RUN; a real call requires unchecking it and explicitly confirming the exact contact/number, processing notice and potential provider charges.

The contact receives a normal telephone call from the configured Twilio number. Ghost identifies itself as an AI, explains that Twilio processes the call and Anthropic processes the words spoken, and discloses provider logging. The first Gather accepts **keypad input only**: no speech is requested or sent to Anthropic before consent. Press 1 to begin; any other response or no response ends without conversation.

After opting in, the contact can talk in short turns. Ghost replies and listens again. The contact can hang up at any time, or press 9 while Ghost is listening. A spoken stop/hang-up request ends the conversation when received as a completed speech turn. Silence, unexpected keypad input or AI failure also ends the test. Pressing a key while already speaking is not guaranteed to interrupt a Gather; hanging up is always the direct telephone control.

The provider request uses a 20-second ring timeout, recording off, and a 180-second connected-call limit. The server additionally limits the request to 180 seconds from creation and at most five AI reply turns. This can end a conversation sooner than three connected minutes. These limits and ring timing depend on provider behavior and need real account validation.

The phone and dashboard have **End this telephone test** controls. The server checks current provider state, cancels a queued/ringing call or completes an active one, and reports uncertainty if the request fails. Closing the phone screen, switching apps or discarding a local retry does not end a submitted call. Use End and verify the provider status; if it is uncertain, use Twilio’s own call controls.

Before considering further stages, supervise: dry run, declined/no consent, positive keypad consent, hearing clarity and pauses, spoken stop, keypad stop while listening, contact hang-up, caller End while ringing and connected, no answer, offline server, provider/AI failure and server restart. Inform the contact beforehand. No tests should involve 000, 112, 106 or any emergency service.

## Security, privacy and recovery

- Every control uses existing device or owner authentication and access restrictions. Real call initiation and enabling live calling require HTTPS. Model/voice navigation can only open the guarded phone screen; it cannot place or end a call.
- A two-minute, source-bound confirmation is tied to the current owner setup. Requests are persisted before dispatch. A lost response retries/retrieves the same exact request and never repeats provider call creation.
- Only one household test may be active at a time, with a maximum of five starts per hour including dry runs. No automatic redial or fallback occurs.
- Calls are checked against the allowlist and prior consent at dispatch, not just in the UI. Saving setup requests an end to active tests. Access revocation, configuration changes and the deadline also request an end.
- Twilio callbacks must pass the official SDK’s full URL/form signature validation, then match the configured account, caller number, destination and bound Call SID. Duplicate fields, unexpected paths, query strings and oversized callback bodies are rejected.
- Telephone speech is untrusted input. The AI receives a separate bounded prompt and only this call’s short conversation, without household memories, transcripts from other chats, contact lists, keys, tools or device-control abilities. Reply text is escaped as XML; it cannot insert dialing or other TwiML actions.
- Ghost requests no audio recording and writes no speech transcript or AI replies to its database or conversation history. Short conversation context and duplicate-turn replies exist in process memory and are cleared on end, deadline or restart. Twilio/Anthropic may retain their own processing/debug logs; review provider settings. Avoid logging webhook bodies in a proxy or monitoring system because they contain recognized speech.
- The database retains contact setup and brief call metadata (destination snapshot, times, consent flag, turns, provider status) but no speech. Test metadata is pruned after 24 hours during subsequent operations; normal backups may retain older data.
- A restart never creates another call or resumes the AI conversation. The server attempts to end known calls using their original connection. Unknown/uncertain creates remain visibly uncertain and may be bound by later signed status callbacks. The original provider duration bound remains important if the server is unavailable.
- Changing Twilio credentials/caller/public origin prevents sending an end request through a different connection. Check the original provider account directly if an end is unconfirmed.
- Provider call completion does not mean assistance was requested, arranged or delivered. Call placement uncertainty, contact consent and provider status are displayed separately. Phone foreground refresh retrieves status; it does not retry calls.

Use a single server process. Do not enable provider-side call recording or debug-body logging for this test. This implementation does not stream telephone audio through the Android microphone, bridge a cellular call, or continuously run an autonomous agent.

## Implementation sources

[Twilio Call resource and call limits](https://www.twilio.com/docs/voice/api/call-resource), [Gather speech/keypad behavior](https://www.twilio.com/docs/voice/twiml/gather), [request signature validation](https://www.twilio.com/docs/usage/security), and [supported telephone voices](https://www.twilio.com/docs/voice/twiml/say/text-speech).

## Development validation and remaining limits

Automated tests mock all telephone placement, end requests and AI responses. They cover dry-run isolation, exact destinations, consent, call/turn limits, idempotent submissions, uncertain creation, end failures, ringing cancellation, restart handling, deadlines, revoked access, setup changes, provider signatures and escaped replies. Admin behavior checks and Android build checks are included.

No real telephone call, AI conversation, contact notification or emergency-service interaction was performed during development. Public HTTPS reachability, Twilio account/number setup, actual keypad/speech recognition, telephone voice, latency, call cutoff and end behavior, Android screen locks and deployed server behavior still require supervised validation.

Stage 7E automatic family calling escalation and Stage 7F emergency-service feasibility remain separate and are not activated by this update.

