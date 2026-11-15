# Section 5: honest action confirmation

Home Assistant writes now validate each service HTTP status and read back state before reporting a confirmed change. Read-back checks on/off/toggle, lock state, cover open/closed, requested brightness and target temperature where directly comparable. Unknown/unavailable/missing state, arbitrary colors, generic services and unsupported effects are not confirmed. A single state read-back is used; delayed devices may legitimately show an uncertain outcome. Check status rather than repeat the write.

Scenes, scripts and conversation requests can be accepted without their effects being verified. Partial batches, response loss after dispatch and server-side errors produce an uncertain outcome, with an explicit instruction not to retry automatically. An HTTP 4xx rejection before any accepted batch step is reported as failed (408 remains uncertain). A known unavailable remote target is not sent; timeouts and missing acknowledgements are uncertain. A successful remote-device reply is an acknowledgement, not independent proof of physical completion.

The tool endpoint adds optional `feedback` with fixed phase/label/detail fields for the current call. It contains no arguments, credentials, raw exception text or action history. Household dispatcher denials have a blocked receipt. Existing clients ignore this extra field and receive the improved spoken/tool result text. The tablet uses it for temporary voice-action feedback. No new unauthenticated endpoint, role, approval bypass, automatic retry or offline action queue is added.

The request still goes through existing household authorization and security confirmation. The whole batch is authorized before the first write. Existing API consumers of `_call` keep its status-code return contract; the voice home tool enforces status checks through `_send`.

Run `python -m pytest tests/test_action_feedback.py -q` for focused checks. CI runs the complete server suite. Tests simulate HA and agent replies; actual home hardware behavior remains untested.
