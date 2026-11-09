# Internet meetings

A two-person session between a clinician on this laptop and a patient anywhere on the internet.

- Audio, video and chat travel **peer to peer** over WebRTC, end-to-end encrypted with DTLS-SRTP and an encrypted data
  channel.
- The **patient's browser records the patient's own microphone** and sends the audio, encrypted, directly to the
  clinician's browser. That browser saves it on this laptop, which transcribes it locally.
- No server ever holds clinical content in readable form.

Same-Wi-Fi meetings keep working as before ([ONLINE_MEETINGS.md](ONLINE_MEETINGS.md)). The design, threat model and
progress log are in [INTERNET_MEETINGS_PLAN.md](INTERNET_MEETINGS_PLAN.md). The manual acceptance checks are in
[INTERNET_MEETINGS_TEST_CHECKLIST.md](INTERNET_MEETINGS_TEST_CHECKLIST.md).

```text
Patient browser ── HTTPS/WSS ──► Tailscale Funnel ──► this laptop: PUBLIC meeting-only server (127.0.0.1:8443)
      │                                               patient page, invitation check, consent, signaling, ICE
      └── WebRTC media + data channel ──────────────► clinician browser (http://localhost:5173)
          direct (STUN), or via a TURN relay that sees only ciphertext       │ loopback
                                                                              ▼
                                       clinician API (127.0.0.1:8000) → whisper.cpp → notes / progress → SQLite
```

## 1. One-time setup

**Tailscale Funnel** (already enabled on the development laptop):

1. Install Tailscale and sign in. Enable MagicDNS and HTTPS certificates in the tailnet admin console.
2. Allow Funnel for this device: the `funnel` node attribute in the tailnet policy file. See
   [tailscale.com/kb/1223/funnel](https://tailscale.com/kb/1223/funnel).
3. Note the device's public name, for example `https://nachovarga.tail5ed395.ts.net`.

**Cloudflare Realtime TURN** (recommended; without it, calls may fail on some networks):

1. Create a TURN key in the Cloudflare dashboard (Realtime → TURN), and note its **key ID** and **API token**.
2. Pricing at the time of writing: $0.05 per GB relayed to the client. Direct connections use no relay.

**`.env`** (git-ignored; never commit it; placeholders are in `.env.example`):

```bash
MEETING_PUBLIC_ORIGIN=https://nachovarga.tail5ed395.ts.net
CLOUDFLARE_TURN_KEY_ID=...
CLOUDFLARE_TURN_API_TOKEN=...
# optional: CLOUDFLARE_TURN_TTL_SECONDS=14400   (never longer than the invitation's remaining lifetime)
# optional secondary provider: TURN_PROVIDER=metered, METERED_APP_SUBDOMAIN=..., METERED_API_KEY=...
```

The keys are read only by the backend. Browsers receive short-lived TURN credentials, generated fresh for each
participant of each meeting and revoked (best effort) when the meeting ends.

## 2. Hold a meeting

```bash
./scripts/run.py
```

That one command starts everything. When `.env` has an `https://` `MEETING_PUBLIC_ORIGIN` and Tailscale is installed,
it runs in **internet mode**:

1. **Checks Tailscale.** It refuses to start unless Tailscale is connected (`tailscale up`) and every server binds to
   `127.0.0.1`.
2. **Builds the patient-only page** (`npm run build:meeting`). It contains nothing from the clinician workspace.
3. **Starts the servers:**
   - the clinician API on `127.0.0.1:8000`, with the speech worker;
   - the public meeting server on `127.0.0.1:8443`, in the same process;
   - the clinician UI on `http://127.0.0.1:5173`.
4. **Turns on Funnel for the public server only:** `tailscale funnel --bg --yes --https=443 http://127.0.0.1:8443`.
5. **Checks the public address.** `/healthz` must answer, and no clinician route (`/api/v1/patients`,
   `/api/v1/sessions`, `/docs`, `/openapi.json`, …) may be served there.
   - If a clinician route ever answers, it **turns Funnel off and stops**.
   - If the address does not answer yet (a new name can take a few minutes), it warns and keeps running.
6. **Turns Funnel off when you stop:** Ctrl+C, closing the terminal, or `kill`. This runs
   `tailscale funnel --https=443 off`, and falls back to `tailscale funnel reset`.

Options:

| Command | Effect |
|---|---|
| `./scripts/run.py --local` | Workspace only: no public server, no Funnel |
| `./scripts/run.py --no-funnel` | Internet servers, but you expose them yourself (`tailscale funnel --bg 8443`) |
| `./scripts/run.py --diag-headers` | Also logs the **names** of headers Funnel adds (never their values) |
| `./scripts/run.py --skip-build` | Reuse the existing patient page build |

Notes:

- In internet mode the backend runs **without auto-reload**, so a code change never cuts a call. Restart after editing
  backend code or `.env`.
- On Linux, Tailscale may refuse configuration changes from a normal user. If Funnel cannot be turned on with "access
  denied", run once: `sudo tailscale set --operator=$USER`.
- `scripts/run_internet.py` is the older variant in which you run the Funnel commands yourself.

Then, in the clinician workspace:

1. **Open the session:** open a patient, then a session, then **Meet online**. Keep **Audio only** ticked unless you
   need video, because it keeps relayed traffic small. Then **Start online meeting**.
2. **Send the invitation:** **Copy link** gives `https://<name>.ts.net/meeting/…#token=…`. Send it to the patient over
   a trusted channel. Anyone holding the link can join as the patient until the meeting ends, at most for 4 hours.
3. **The patient joins:** they open the link and read the consent text. They tick consent, check the microphone, and
   **Join meeting**.
4. **Compare the codes:** once connected, both screens show a **verification code**. Ask the patient to read theirs
   aloud. Choose **Codes match** only if it is identical. If the codes differ, end the call: someone may be
   intercepting it.
5. **Check the connection type:** the status line says **Direct connection** or **Relayed connection (TURN)**.
6. **Recording:** it starts when both are connected.
   - The patient's audio is recorded on the patient's device.
   - After each minute, the patient's chunk arrives over the data channel and is saved here.
   - The clinician's panel counts "patient-device chunks received".
7. **End meeting:** it waits briefly for the patient's last partial minute, then uploads any remaining audio and closes
   the invitation.

In the session's recording list, patient audio is labelled **recorded on the patient's device**. If that recording
was unavailable for part of the call, the audio is labelled **network-received audio (fallback)**.

## 3. What each party can see

| Party | Can see | Cannot see |
|---|---|---|
| Tailscale Funnel relays | The patient's IP, timing, volume, the `*.ts.net` name | Content: TLS terminates on this laptop |
| TURN relay (Cloudflare) | Both IPs, timing, volume | Media, chat, audio chunks: encrypted end to end between the browsers |
| Signaling (the public server on this laptop) | Meeting id, token hashes, SDP and ICE (including IP addresses) | Chat, media, audio, recording control: these use the data channel |
| The patient's device | Its own recording until acknowledged (encrypted in browser storage) | Anything about other patients |
| Anyone with the invitation link | Can join as the patient while it is valid | The clinician workspace, other meetings |
| Internet scanners | `/healthz` and the static patient page | The clinician API: it is on a different socket and also refuses public-host and forwarded requests |

**What the verification code proves.** It comes from both browsers' DTLS certificate fingerprints, so it changes if
anything on the signaling path swapped keys. It **cannot** protect against a compromised laptop, or against patient-page
JavaScript altered on this laptop or in the tunnel. That JavaScript is served from this laptop, so trusting the patient
page means trusting this laptop and the Funnel TLS certificate.

**What leaves the laptop's control:**

- The TURN relay sees connection metadata.
- The patient's browser holds encrypted, pending audio until it is acknowledged.
- The invitation link travels however you send it.

Clinical processing (Whisper, notes, progress) is unchanged and fully local.

## 4. Limits

- **Two participants per room, one process.** Live rooms are held in memory, so the clinician API and the public
  server must run in the same process (the launcher does this).
- **Patient-side recording needs a modern browser** with AudioWorklet, IndexedDB and Web Crypto on HTTPS: current
  Chrome, Edge, Firefox or Safari. Otherwise the network-received audio is used, and labelled as such.
- **Patient outbox key:** it exists only in the page's memory. If the patient reloads the page, chunks waiting to be
  sent cannot be decrypted and are deleted. The fallback covers that time.
- **The fallback rule:** a network-received chunk is used where patient-device chunks cover less than 50% of its
  minute. `?compare=1` on the clinician's meeting page keeps both copies, for the manual quality check only.
- **Clock alignment:** patient chunk offsets come from a ping/pong clock estimate, and each chunk stores its
  uncertainty (half the round-trip time) and the RTT. Expect tens of milliseconds of uncertainty on typical connections.
- **Bandwidth and reliability:**
  - Funnel has unpublished bandwidth limits.
  - Cloudflare's TURN limits are per allocation (about 50–100 Mbps). Audio-only calls are far below that.
  - Without TURN, calls between restrictive networks (some mobile carriers, corporate Wi-Fi) may not connect.
- **Not a production telehealth service:** there is no clinician authentication, reviewer names are self-reported, and
  this is an FYP prototype.

## 5. Troubleshooting

| Symptom | Check |
|---|---|
| Launcher refuses to start | The message names the problem: usually Tailscale is not connected (`tailscale up`), `MEETING_PUBLIC_ORIGIN` is not `https://`, or a port is in use. |
| "Could not turn on Tailscale Funnel… access denied" | Run once: `sudo tailscale set --operator=$USER`, then start again. |
| Funnel still on after a crash | `tailscale funnel status`; turn it off with `tailscale funnel reset`. |
| Public checks fail at `/healthz` | `tailscale funnel status` must show `https://<name>.ts.net` proxying to `http://127.0.0.1:8443`. DNS for a new name can take up to 10 minutes. |
| A clinician route answers publicly | Stop immediately: `tailscale funnel reset`. Funnel is pointing at the wrong port; only 8443 may be exposed. |
| "No TURN relay is configured" banner | TURN keys are missing from `.env`. Direct connections may still work. |
| "The TURN relay could not be reached" | Cloudflare API error or no internet. The call falls back to STUN only. |
| Patient sees "invitation is closed" | The meeting ended or is over 4 hours old: create a new meeting. |
| Codes differ | End the call, create a new meeting and send the link again over a trusted channel. |
| No "patient-device chunks received" | The patient's browser may not support recording (fallback audio is used), or the data channel dropped. Chunks are resent within 15 s of reconnecting. |
| Which headers does Funnel add? | `./scripts/run.py --diag-headers` logs header **names** (never values) for public `/healthz` requests. |

## 6. Tests

```bash
.venv/bin/python -m pytest -q tests/api/test_meeting_public.py tests/api/test_turn.py tests/api/test_meetings.py tests/test_secrets_guard.py
cd apps/web
npm run test:unit                 # protocol, verification code, chunk transfer, clock sync
npm run test:meeting:internet     # two browsers, public server on HTTPS, mocked ICE, about 1.5 minutes
npx playwright test e2e/meeting.spec.ts   # same-Wi-Fi suite (includes patient-device recording)
```

The internet browser test uses a local certificate in place of Funnel's TLS, and `TURN_PROVIDER=none`, so it never
contacts Tailscale or Cloudflare. It asserts:

- the matching verification code;
- chat only on the data channel, with a spy on every signaling frame;
- patient-device chunks, contiguous with no gap;
- no fallback upload;
- 404 for clinician routes on the public origin.

The real-network checks are manual ([checklist](INTERNET_MEETINGS_TEST_CHECKLIST.md)).
