# Optional household voice recognition

Deploy this server change before installing the companion phone/tablet changes.
Rebuild the server image: its model-download step now installs a checksum-pinned
WeSpeaker speaker-embedding model. No external voice service is used for matching.
This feature identifies an enrolled speaker; it does not fine-tune speech-to-text,
clone anyone's voice, or change Ghost's selected speaking voice.

After adding a person through tablet Visual ID, choose **Learn voice**. Ghost reads
four phrases; the person taps Record and repeats each naturally. Each capture lasts
six seconds. Skip is optional. Phone and tablet Settings → Voice also provide
**Learn or re-record a voice** for an existing Visual ID person; starting there
requires the server owner's password and existing two-step code when enabled.
Face-scan-issued enrolment tickets are limited to the person and paired device,
expire after ten minutes, and cannot be used by another device.

Recognition is off by default on each device. Completing enrolment enables it there;
Settings → Voice → **Recognise household voices** controls it independently. While
enabled, the existing conversation microphone supplies short 16 kHz clips to this
Ghost Server, at most once every 15 seconds. It opens no extra background microphone.
Ghost's playback is excluded; muted and text-only sessions do not submit samples.
The matched name/unknown status becomes conversation context. Matching failures do
not interrupt normal conversation or voice playback.

Only normalized voice embeddings are saved in the existing server database, with
person/device/model identifiers and sample count/date. PCM recordings are not saved.
Partial enrolment embeddings remain only in memory, become inaccessible after ten
minutes and are pruned when new tickets are issued. Restarting clears them. A new
profile replaces a previous one only after all four consistent samples pass; phone
and tablet profiles coexist for the same person. Disable recognition to stop new
matching uploads. To erase all of a person's voice profiles without their face ID,
use authenticated POST `/admin/api/people/{id}/voice/delete`; deleting the Visual ID
person also removes all their voice profiles and pending enrolments. Database
backups retain their normal retention policy.

Unknown and ambiguous speakers remain unidentified. Current cosine cut-off is .90
with .08 separation from other enrolled people; these are conservative starting
values, not a household accuracy guarantee. Four recordings must also agree (.88).
Noise, similar voices, illness, different microphones and short responses may return
unknown. Re-record in a quiet room on the device used for conversation. Speaker ID
is personalisation only: it does not authenticate, unlock, approve messages, change
device permissions, or bypass private-message consent.

## Model provenance

WeSpeaker `wespeaker_en_voxceleb_resnet34_LM.onnx`, exported by sherpa-onnx:
https://github.com/k2-fsa/sherpa-onnx/releases/tag/speaker-recongition-models

SHA-256: `e9848563da86f263117134dfd7ad63c92355b37de492b55e325400c9d9c39012`

Runtime: `sherpa-onnx==2.0.1`. Upstream projects and their Apache-2.0 licenses:
https://github.com/wenet-e2e/wespeaker and https://github.com/k2-fsa/sherpa-onnx.
Their license copies are included under `third_party/voice/` in this repository/image.

## Validation and rollout

Server suite: 53 passed, one skipped. Includes device/person ticket binding, owner
approval route, four-sample publication, retry/order/duplicate handling, poor audio,
known/unknown/ambiguous results, cross-device profiles, and deletion.
The checksum-verified real model produced 256-dimensional embeddings, .920 similarity
for a held-out same-speaker sample and .834 for a different speaker using upstream
example recordings. This is a smoke test, not household calibration or anti-spoofing.

Focused Kotlin compilation passed for both recording screens, recognition probes,
settings storage and real ServerApi implementations, plus the tablet face-enrolment
handoff, using cached Android API35/AndroidX libraries. Unrelated app services/UI
helpers were substituted for that check. Full APK builds, physical phone/tablet
microphone/TTS behaviour, NAS image deployment and recognition in your home remain
untested. Check Skip, permission refusal, interruption/resume, re-record, two people,
an unknown speaker, and normal voice/typed replies after installation.

The updated live voice sessions also passed focused compilation; all 39 existing
phone and 19 tablet tests passed, including phone message privacy and greeting tests.
