# Internet meetings: manual real-network checklist

Run these on real devices before relying on internet meetings. Use fictional conversations only. For each check,
record the date, the devices and browsers, the networks, the result and notes in the table at the end. The automated
tests cannot cover real NAT, mobile networks, Funnel or TURN.

## Before every session

- [ ] `.env` has `MEETING_PUBLIC_ORIGIN` (https) and the Cloudflare TURN keys. Check with `./scripts/setup.sh --check`
      or by starting the launcher. Never print the key values.
- [ ] `git status` shows no `.env`.
- [ ] Run `.venv/bin/python scripts/run_internet.py --diag-headers`, then `tailscale funnel --bg 8443`, then press
      Enter. **All public checks PASS.**
- [ ] Note the header names logged for the public `/healthz` request (diagnostic). Record whether Funnel adds
      `X-Forwarded-For` or similar.
- [ ] Afterwards: `tailscale funnel reset`, and `tailscale funnel status` shows nothing.

## Checks

1. **Home Wi-Fi ↔ mobile data.**
   - Setup: clinician on home Wi-Fi; patient phone on mobile data with Wi-Fi off.
   - The call connects, both hear each other, and the connection type is shown.
   - Record: direct or relayed.
2. **Two different ISPs.**
   - Setup: the patient on another household's or a café's network (not the same router).
   - Record the connection type, and whether the call connects at all with TURN disabled (remove the keys temporarily
     and restart).
3. **Connection type and usage.**
   - After the call, "Meet online" shows this month's meetings and relayed MB.
   - Compare the relayed MB with Cloudflare's dashboard for the same call (expect the same order of magnitude).
4. **30-minute stability.**
   - Audio only, 30 minutes of conversation.
   - No disconnects.
   - The clinician's panel counts about 30 patient-device chunks.
   - The session lists 30 or 31 patient chunks labelled **recorded on the patient's device**, with no gaps in the
     timeline.
5. **30-second network drop.**
   - During recording, disable the patient's network for about 30 s, then re-enable it.
   - The call recovers, or the patient rejoins.
   - Pending chunks are delivered within about 15 s of the data channel reopening: the count catches up, and no
     minute is missing.
   - Record whether any fallback (network-received) chunk appeared, and for which minute.
6. **Verification code.**
   - Both screens show the same 6 digits, and the patient reads it aloud.
   - **Codes match** is recorded in the session audit (`MEETING_VERIFICATION_CONFIRMED`).
   - Optional: open the patient link in a second, fresh browser after the first patient leaves. A new code appears.
7. **Transcript quality: patient device vs network-received.**
   - On the clinician laptop, open the meeting page with `?compare=1` added before the `#`, for example
     `http://localhost:5173/meeting/<id>?compare=1`. Both copies of every patient minute are then saved: the
     patient-device one and the network-received one.
   - The patient reads a fixed passage. After transcription, compare the word errors of the two sources in the session
     transcript; the recording list labels each source.
   - Use comparison mode only for this check: it duplicates the patient's lines in the transcript.
   - Record which source gave fewer errors, and the conditions (headphones, speakers, network quality).
8. **Patient page hygiene.**
   - On the patient device, the browser's developer tools show:
     - requests only to `https://<name>.ts.net`;
     - no requests to third-party origins (TURN traffic is WebRTC, not HTTP);
     - no CSP violations in the console.
9. **End and expiry.**
   - After **End meeting**, the patient link shows "invitation is closed".
   - A link older than 4 hours is refused.

## Results

| Date | Check | Clinician device / network | Patient device / browser / network | Connection type | Result | Notes |
|---|---|---|---|---|---|---|
| | | | | | | |
