# Conversation scope and verification

Raw conversation transcripts stay in their source-device history. Fresh session
prompts no longer copy snippets from other devices, including a private phone
into a shared tablet. Same-device text history, admin audit records, deliberately
shared household facts/rules and the existing authorised handoff flow remain.
There is no database migration or transcript deletion.

This is a device-level boundary, not individual speaker isolation within one
shared device. Shared household memory is still shared: private information
should not be deliberately saved there. Existing household access controls
remain in force. Cross-device conversational continuity should use handoff.

The photo scanner now ignores hidden NAS thumbnail folders relative to the
selected photo root. A # or @ in an ancestor's folder name no longer hides every
photo below the explicitly selected root.

New regression tests exercise device transcript isolation, deliberately shared
facts, source-device log retention and photo-root filtering. The complete server
suite passes locally: 120 passed, one skipped. The new GitHub checks run the
suite on pull requests as well as pushes. No real household database was used
for these tests, and no running server has been modified or deployed.
