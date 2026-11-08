# Internet meetings with patient-side clean recording: implementation plan

**Status:** Phase 0 plan, written 8 November 2026. **Waiting for approval** and for answers to the open questions (§11).
**Branch:** `feat/internet-meetings`, from `progress-monitoring` at `877b9cb`.
**Persistent rules:** the "Internet meetings module" section of [`CLAUDE.md`](../CLAUDE.md).

This plan extends the existing same-Wi-Fi meeting ([ONLINE_MEETINGS.md](ONLINE_MEETINGS.md)) so that a clinician and a
patient can meet over the internet:

- the patient reaches a separate, meeting-only public server on the laptop through Tailscale Funnel;
- media and chat travel peer-to-peer over WebRTC (DTLS-SRTP, plus an encrypted data channel), through a TURN relay when
  needed;
- the patient's browser records its own microphone and sends the chunks to the clinician over the data channel.

Everything clinical is decrypted, stored and processed only on the clinician's laptop.

---

## 1. Baseline (8 November 2026)

| Item | Finding |
|---|---|
| Git | Branch `feat/internet-meetings` from `progress-monitoring` (`877b9cb`). The only change is `apps/web/tsconfig.app.tsbuildinfo`, a build artifact my builds rewrite; it is not committed. |
| Backend tests | `pytest -q`: **302 passed** |
| Frontend build | passes |
| Meeting Playwright (`npx playwright test e2e/meeting.spec.ts`) | **3 passed, 1 failed.** The failure is the minute-chunk test: the second chunk's offset was 16 ms from the end of the first, and the test allows ≤ 1 ms. It reproduced when run alone (8 ms gap), so load is not the cause. It is **pre-existing**; no meeting code has changed. The likely cause is the clinician's two worklet nodes starting their slices at slightly different render frames relative to the shared epoch. |
| Secrets | `.env` is ignored (`.gitignore:11`) and not tracked. `MEETING_ICE_SERVERS`: set. `CLOUDFLARE_TURN_KEY_ID`, `CLOUDFLARE_TURN_API_TOKEN`: **missing** from `.env`. |
| Tools | `tailscale` 1.102.5 is installed. It is not run by Claude. |
| Dependencies | `httpx` is declared only in the `dev` extra. Using it at runtime for TURN credentials needs approval (Q1). No other new dependency is planned. |
| Progress module | Not touched. No command in this plan loads the LLM or the Whisper model (tests use fakes; Playwright runs with the speech worker disabled). |

### 1.1 What exists today

- **Clinician API (`services/api/main.py`):**
  - It binds to loopback.
  - The `protect_lan_workspace` middleware trusts loopback clients and an `X-Pshynox-Client-IP` header set by the Vite
    proxy, and only from loopback.
  - Non-local clients may reach only `/api/v1/meetings/meeting-<32hex>`, `…/end` and `…/chunks/<uuid>`.
  - The middleware is HTTP-only, so WebSocket routes are not covered by it.
- **Meeting routes (`services/api/meeting_routes.py`):**
  - Role tokens are random 32-byte values. The database stores only their SHA-256 (`host_hash`, `guest_hash`).
  - Invitations expire after 4 hours.
  - Only one connection per role is allowed.
  - The token is sent in the first WebSocket frame, never in the URL.
  - Signaling message types are `offer`, `answer`, `ice`, `chat`, `media`, `leave`, `recording`. **Chat and recording
    state currently travel on the signaling socket.**
  - The room registry is an in-memory dict inside `build_meeting_router`, so it requires a single process.
  - Chunk upload is idempotent by UUID, accepts 16-bit mono WAV of at most 61 s, and is host-only and consent-gated.
- **Frontend (`MeetingRoom.tsx`):**
  - The clinician captures its own microphone and the *network-received* patient audio with the AudioWorklet in
    `public/meeting-pcm.js` (60 s chunks, no gaps).
  - Chunks are queued in IndexedDB, then uploaded.
  - The patient never records.
  - The invitation token is in the URL fragment, which is never sent to the server.
- **LAN launcher (`scripts/run_lan.py`):**
  - It creates a local CA and server certificate.
  - It runs the API on 127.0.0.1 and Vite on `0.0.0.0` over HTTPS.
  - It sets `MEETING_PUBLIC_ORIGIN` to the Wi-Fi address.

### 1.2 External facts checked (8 November 2026)

- **Cloudflare Realtime TURN** (developers.cloudflare.com/realtime/turn/):
  - The request is `POST https://rtc.live.cloudflare.com/v1/turn/keys/{key_id}/credentials/generate-ice-servers` with a
    bearer token and body `{"ttl": n}`; it returns 201.
  - The response is `iceServers`: one STUN entry (`stun:stun.cloudflare.com:3478`) and one TURN entry. The TURN entry has
    UDP on 3478 and 443, TCP on 3478 and 80, TLS on 5349 and 443, plus `username` and `credential`.
  - The docs say: port 53 "is known to be blocked by web browsers… you might want to filter out the URL with port 53".
  - Revocation is documented: `POST …/credentials/{username}/revoke`, returning 204.
  - Price: $0.05 per GB outbound from Cloudflare to the client when the SFU is not used.
  - Per-allocation limits: about 50–100 Mbps and 5–10 kpps.
- **Tailscale Funnel** (tailscale.com/kb/1223, /1311, /1312):
  - TLS terminates on the device; the relay servers do not decrypt.
  - Public ports are 443, 8443 and 10000. Only `http://127.0.0.1` backends are supported.
  - `tailscale funnel --bg <port>` runs persistently (including after reboot) until `tailscale funnel reset`. Status is
    `tailscale funnel status`.
  - Funnel traffic carries **no identity headers**. The docs **do not state** whether `X-Forwarded-For` or similar
    headers are added, and **do not mention WebSockets**. Both are verified manually in Phase 1 (§10).
  - Bandwidth limits exist but are not configurable or published.

---

## 2. Architecture

```text
Patient browser ── HTTPS/WSS ──► Tailscale Funnel (TLS ends on the laptop)
                                     │ http://127.0.0.1:8443
                                     ▼
       ┌──────────────── one Python process (scripts/run_internet.py starts it) ────────────────┐
       │ PUBLIC app  services/meeting_public/app.py  127.0.0.1:8443   ← every request untrusted   │
       │   /meeting/<id> (patient-only bundle), /healthz,                                         │
       │   /api/v1/meetings/<id> (guest view), …/consent, …/ice-servers, …/signal (WS)            │
       │        │ only via services/meeting_public/gateway.py (narrow interface)                  │
       │        ▼                                                                                 │
       │ meeting hub (rooms, relay) ◄── shared ──► CLINICIAN API services/api/main.py 127.0.0.1:8000 │
       └─────────────────────────────────────────────────────────────────────────────────────────┘
                                     ▲ loopback only
Clinician browser (http://localhost:5173, Vite on 127.0.0.1) ──┘
Patient browser ◄═══ WebRTC: DTLS-SRTP media + data channel "pshynox-v1" ═══► Clinician browser
                     direct via STUN, else relayed by TURN (relay sees ciphertext only)
Clinician browser ──► clinician API chunk upload → recordings → whisper.cpp → notes / progress (unchanged)
```

Design decisions:

1. **Two ASGI apps, two sockets, one process.**
   - `services/meeting_public/serve.py` runs two `uvicorn.Server` instances in one event loop. The clinician API binds
     `127.0.0.1:8000` with its normal lifespan (migrations, speech worker). The public app binds
     `127.0.0.1:MEETING_PUBLIC_PORT`.
   - Rooms live in one process, so both apps share the in-memory hub.
   - The clinician app is never mounted on the public socket, and the public app has no route that reaches clinician
     code.
   - The alternative, a separate process that forwards over loopback, is rejected: anything forwarding over loopback
     would look like the trusted clinician to the existing API.
2. **The hub moves out of `meeting_routes.py`** into `services/api/meeting_hub.py`, a class with `join`, `relay`,
   `leave` and `close_meeting`. The clinician WebSocket keeps its current path and behaviour through the hub, so LAN mode
   is unchanged.
3. **Narrow interface (`services/meeting_public/gateway.py`).** It is the only module the public app imports from the
   backend. It exposes:

   | Function | Purpose |
   |---|---|
   | `guest_view(mid, token)` | Status and expiry only |
   | `record_guest_consent(mid, token)` | Store the patient's consent |
   | `guest_join(mid, token, ws)` | Join the hub room |
   | `guest_ice_servers(mid, token)` | Short-lived ICE credentials |
   | `health()` | Liveness only |

   - Every function accepts **only the guest token**. Host tokens are rejected (`MEETING_ACCESS_DENIED`).
   - It uses `services.api.db` for its database session, never `services.api.main`.
   - A test imports the public app in a fresh interpreter and asserts that none of these is in `sys.modules`:
     `services.api.main`, `patient_routes`, `progress_routes`, `research_routes`, `note_generation_routes`,
     `speech_routes`.
4. **Patient-only frontend bundle.**
   - A second Vite entry, `apps/web/meeting.html` with `src/meeting-main.tsx`, renders only `MeetingRoom`.
   - `npm run build:meeting` builds it into `apps/web/dist-meeting/`.
   - The public app serves only that folder. The clinician UI code is never served publicly.
   - No new npm dependency.
5. **The clinician UI in internet mode** is the Vite dev server on `127.0.0.1:5173` over plain HTTP. `localhost` counts
   as a secure context for the microphone and AudioWorklet, so no local CA is needed.
6. **Defence in depth on the clinician API** (internet mode). The `protect_lan_workspace` middleware, plus a new check in
   the clinician WebSocket handler, reject a request when:
   - its `Host` is not `localhost`, `127.0.0.1` or `[::1]` (any port); or
   - it carries `X-Forwarded-For`, `X-Forwarded-Host`, `Forwarded` or `Tailscale-Funnel-Request` (whichever of these
     appear in practice).

   So if Funnel were pointed at port 8000 by mistake, the public hostname would get 403 on every route. LAN mode keeps
   its current rules.

---

## 3. Threat model

| Party | Can see | Cannot see |
|---|---|---|
| Tailscale Funnel relay | Patient IP, timing, volume, SNI (`*.ts.net` name) | Contents: TLS terminates on the laptop |
| TURN relay (Cloudflare) | Both peers' IPs, timing, volume, allocation username | Media, chat or audio chunks: DTLS-SRTP and SCTP-over-DTLS ciphertext |
| Signaling (our public app on the laptop) | Meeting id, token hashes, SDP and ICE candidates (including IP addresses), timing | Media, chat, audio. After Phase 3 neither chat nor recording control passes through signaling. |
| Patient device | Its own recording until acknowledged (encrypted in IndexedDB, best effort) | Anything about other patients: the public app exposes no records |
| Someone holding the invitation link | Can join as the patient while it is valid (bearer token, 4 h, one connection at a time, closed when the meeting ends) | Clinician routes or other meetings |
| Internet scanners | `/healthz`, the patient bundle (static code, no data), invitation endpoints protected by 128-bit meeting ids and 256-bit tokens | Clinician API: it is on a different socket, and a Host/forwarding check backs that up |

**Verification code.**

- **What it is:** both browsers compute a 6-digit code from the two DTLS certificate fingerprints in the SDP. The
  clinician asks the patient to read it aloud and confirms the match; the confirmation is audited.
- **What it detects:** a signaling path that swapped keys, i.e. someone in the middle relaying two separate DTLS
  sessions.
- **Its limit:** it does **not** protect against a compromised laptop, or against JavaScript served by a compromised
  laptop or tunnel. The patient's page code comes from the laptop, so trust in the code reduces to trust in the laptop
  and the Funnel TLS certificate.

**Not addressed:**

- Clinician authentication (unchanged prototype limit).
- Malware on either device.
- Traffic analysis.
- An invitation link forwarded by the patient.

---

## 4. Public surface (Phase 1)

| Route | Auth | Limits |
|---|---|---|
| `GET /healthz` | none | Returns `{"status":"ok"}` only: no version, no paths, no counts |
| `GET /meeting/<meeting-id>` and `/assets/*`, `/meeting-pcm.js` | none (static) | Only files from `dist-meeting/`. No directory listing, no source maps. |
| `GET /api/v1/meetings/<id>` | guest token (`Authorization: Bearer`) | Returns `{id, role: "CLIENT", status, expires_ms, audio_only}`. Nothing about the session or patient. |
| `POST /api/v1/meetings/<id>/consent` | guest token, `Origin` must equal `MEETING_PUBLIC_ORIGIN` | Body ≤ 1 KB `{"recording_consent": true}` |
| `GET /api/v1/meetings/<id>/ice-servers` | guest token | TTL ≤ remaining invitation lifetime (Phase 2) |
| `WS /api/v1/meetings/<id>/signal` | guest token in the first frame; `Origin` checked on upgrade | Messages ≤ 64 KB, 200 per 10 s; only `offer`, `answer`, `ice`, `leave` after Phase 3 |
| anything else | – | 404 with an empty JSON body |

**Rate limits.** These are in memory, with sliding windows. Funnel gives no client IP, so they are keyed by route and
meeting id plus a global per-route limit. Proposed defaults:

- lookup and consent: 30 requests per minute per meeting, 300 per minute global;
- ICE: 10 per minute per meeting;
- WebSocket upgrades: 10 per minute per meeting;
- failed token checks: 20 per 10 minutes per meeting, then 429 for 10 minutes.

The meeting id is 128-bit and the token 256-bit, so the limits protect resources rather than guard against guessing.

**Headers on every response:**

- `Content-Security-Policy: default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data: blob:;
  media-src 'self' blob: mediastream:; connect-src 'self'; worker-src 'self'; font-src 'self'; base-uri 'none';
  form-action 'none'; frame-ancestors 'none'` (no inline script, no third-party origin; checked against the built
  bundle);
- `Referrer-Policy: no-referrer`;
- `X-Content-Type-Options: nosniff`;
- `Cross-Origin-Opener-Policy: same-origin`;
- `Cross-Origin-Resource-Policy: same-origin`;
- `Permissions-Policy: camera=(self), microphone=(self), geolocation=()`;
- `Strict-Transport-Security: max-age=31536000` (HTTPS only via Funnel);
- `Cache-Control: no-store` on API routes.

There is no CORS: no `Access-Control-*` headers, and cross-origin `Origin` values are rejected.

**Configuration:**

- `MEETING_MODE=lan|internet` (default `lan`).
- `MEETING_PUBLIC_ORIGIN`:
  - normalised by lowercasing the host and stripping the trailing slash and any path;
  - must be `https://` in internet mode, or startup fails;
  - used for exact `Origin` checks and invitation links.
- `MEETING_PUBLIC_PORT` (default 8443). Neither the clinician API nor the public app binds to anything but 127.0.0.1.

**Launcher `scripts/run_internet.py`:**

1. Validate configuration. Refuse to start if any bind host (API, public app, Vite) is not loopback.
2. `npm run build:meeting`.
3. Start the combined server process, and Vite on `127.0.0.1:5173` with `VITE_API_TARGET=http://127.0.0.1:8000`.
4. Print `tailscale funnel --bg 8443` and how to stop it (`tailscale funnel reset`). It never runs tailscale itself.
5. After the user presses Enter to confirm Funnel is up, check that `MEETING_PUBLIC_ORIGIN/healthz` returns 200.
6. Check that the public origin does **not** serve `/api/v1/patients`, `/api/v1/sessions`, `/docs`, `/openapi.json` or
   `/api/v1/progress/registry` (each must be 404).
7. Print the clinician URL. On exit, remind the user to run `tailscale funnel reset`.

`scripts/run_lan.py` is unchanged apart from the shared hub.

---

## 5. NAT traversal (Phase 2)

- **Who gets ICE servers:** the clinician through `GET /api/v1/meetings/<id>/ice-servers` on the clinician API (host
  token), and the patient through the public app (guest token). Both require an open, unexpired meeting.
- **Provider `cloudflare` (default when configured):**
  - `httpx.post(…/generate-ice-servers, json={"ttl": ttl}, timeout=5)`;
  - `ttl = min(CLOUDFLARE_TURN_TTL_SECONDS (default 14400), remaining invitation seconds)`;
  - the response's `iceServers` is passed through unchanged, except that URLs on port 53 are dropped;
  - nothing is cached across meetings, so each request generates fresh credentials;
  - logs record only `turn.credentials ok|failed latency_ms`, never the token, username or credential.
- **Revocation:** when a meeting ends, credentials issued for it are revoked as a best effort. The usernames are kept only
  in process memory, keyed by meeting (Q8).
- **Provider `metered`:** optional, off by default. Configured with `TURN_PROVIDER=metered`, `METERED_APP_SUBDOMAIN` and
  `METERED_API_KEY`, calling `https://{subdomain}.metered.live/api/v1/turn/credentials?apiKey=…`.
- **On failure or no configuration:** return STUN-only (`stun:stun.cloudflare.com:3478`) with `warning:
  "connection may fail on some networks"`, which the UI shows.
- **Audio only:** a per-meeting option, `audio_only` on `MeetingRecord`. It defaults to on when `APP_ENV=development`.
  It is chosen when the clinician creates the meeting, and both clients skip the camera.
- **Connection type:**
  - After connecting, each client reads `getStats()` for the selected candidate pair and shows *direct* or *relayed*.
  - At call end, the clinician posts `{relayed, local_type, remote_type, bytes_sent, bytes_received, duration_s}` to
    `POST /api/v1/meetings/<id>/stats`. No IP addresses are sent or stored.
  - The meeting entry panel shows a monthly total: meetings, relayed meetings, relayed MB, and an estimated cost of
    $0.05/GB (Q9).

---

## 6. Data channel protocol `pshynox-v1` (Phases 3–4)

- **The channel:** reliable and ordered, created by the clinician after the peer connection opens, labelled
  `pshynox-v1`.
- **Framing:** every message is a UTF-8 JSON text frame of at most 16 KiB, of the form `{"v":1,"t":"<type>",…}`.
  Unknown versions or types are ignored and counted. `src/features/meetings/protocol.ts` encodes and validates them;
  pure functions, unit-tested.

| Type | Direction | Fields |
|---|---|---|
| `hello` | both | `role`, `caps: {record: bool, idb: bool}`, `app: "<build id>"` |
| `chat` | both | `id`, `text` (≤ 2,000 chars) |
| `media` | both | `camera`, `mic` (moved off signaling) |
| `recording_start` | clinician → patient | `meeting_ms` (the clinician's meeting clock at send) |
| `recording_stop` | clinician → patient | `meeting_ms` |
| `clock_ping` / `clock_pong` | patient → clinician / back | `seq`, `t0` (patient `performance.now()`); pong adds `meeting_ms` |
| `chunk_meta` | patient → clinician | `chunk_id` (UUID), `offset_ms`, `offset_uncertainty_ms`, `rtt_ms`, `duration_ms`, `rate`, `bytes`, `parts`, `sha256` |
| `chunk_part` | patient → clinician | `chunk_id`, `index`, `data` (base64, ≤ 12 KiB raw so the frame stays ≤ 16 KiB) |
| `chunk_ack` | clinician → patient | `chunk_id`, `status: "saved" \| "rejected"`, `reason?` |
| `verify` | both | `code_confirmed: bool` (the clinician's confirmation is mirrored to the patient) |
| `bye` | both | `reason` |

**Rules:**

- **Signaling** carries only `offer`, `answer`, `ice` and `leave` after Phase 3. The server drops anything else, and a
  test asserts that `chat` never reaches the peer through signaling.
- **Backpressure:** the sender pauses when `bufferedAmount > 1 MiB` and resumes on `bufferedamountlow` (threshold
  256 KiB).
- **Reassembly (clinician):**
  - parts are collected by index; duplicates are ignored, and reordering is tolerated in the reassembler for
    robustness;
  - the full chunk's SHA-256 is verified;
  - it is uploaded to the existing `POST /api/v1/meetings/<id>/chunks/<chunk_id>` with
    `speaker_role=CLIENT&source=patient_device&offset_ms=…`;
  - `chunk_ack` is sent only after a 201, or a duplicate's 200.
  - A hash mismatch gets `chunk_ack rejected`, and the patient resends.
  - Incomplete chunks are dropped after 60 s without progress.
- **Verification code:**
  - collect the `a=fingerprint:` values from the local and remote SDP;
  - normalise them (upper-case hex; the algorithm prefix is kept);
  - sort them, join with `\n`, and take the SHA-256;
  - read the first 4 bytes as an unsigned integer, mod 1,000,000, zero-padded to 6 digits.

  Both sides get the same code, and any changed fingerprint changes it. The clinician's "codes match" button calls
  `POST /api/v1/meetings/<id>/verification`, which writes the audit event `MEETING_VERIFICATION_CONFIRMED` (or `…_MISMATCH`
  plus an alert). The code itself is not stored.

---

## 7. Patient-side recording (Phase 4)

- **Consent first:** the patient page shows the consent text (§7.1). `POST …/consent` must succeed before the patient can
  join, and the patient browser never records before `recording_start` *and* local consent. The server already refuses
  chunks until `guest_consented`.
- **Capture:** the existing `meeting-pcm.js` worklet runs in a 16 kHz `AudioContext` on the patient's microphone track,
  producing 60 s mono 16-bit WAV chunks and a final partial chunk on stop.
- **Microphone constraints (Q6):** `echoCancellation: true` (keeps the clinician's voice out if speakers are used),
  `noiseSuppression: false` and `autoGainControl: false`. Browser noise suppression can clip speech onsets that Whisper
  handles better unprocessed, and gain pumping changes levels mid-chunk. This is documented, and the same track is used
  for the call.
- **Outbox on the patient device:**
  - IndexedDB `pshynox-patient-outbox`, keyed by `chunk_id`;
  - each chunk is encrypted with AES-GCM-256 using a non-extractable `CryptoKey` generated per meeting and held only in
    memory, with a random 96-bit IV per chunk;
  - a chunk is deleted only after `chunk_ack saved`;
  - on data-channel reconnect within the same page, unacknowledged chunks are resent.
  - **Limit (Q5):** a page reload loses the key, so leftover ciphertext cannot be decrypted. It is deleted, and that time
    is covered by the clinician's fallback (below).
- **Clock:**
  - after `recording_start` the patient sends 5 `clock_ping`s;
  - the sample with the lowest RTT gives `meeting_ms ≈ pong.meeting_ms + RTT/2` at receipt;
  - audio frames are mapped to `performance.now()` with `AudioContext.getOutputTimestamp()`;
  - each chunk's `offset_ms` is the meeting time of its first frame, with `offset_uncertainty_ms = RTT/2`;
  - the clock is re-synced every 5 minutes.
  - A test-only drift report compares the summed chunk durations with the patient's wall clock, in ppm.
- **Fallback and source labels:**
  - The clinician keeps capturing the network-received patient audio as today, but into a separate **fallback** store
    that is not uploaded straight away.
  - Every 60 s, and at the end of the call, the fallback chunk for a window is uploaded with `source=network_received`
    only if patient-device chunks cover less than 50% of that window (Q4). Otherwise it is discarded.
  - Whenever the patient reports `caps.record=false`, or the data channel is down, fallback chunks are uploaded directly.
  - The clinician's own microphone gets `source=clinician_local`.
  - The source is shown next to each recording in the session's transcript sources.
- **Backend (additive):**
  - Migration `0009_internet_meetings` adds to `meeting_chunks`: `source` (default `clinician_local`, with existing rows
    `NULL` meaning legacy), `offset_uncertainty_ms` and `rtt_ms` (both nullable).
  - It adds to `meetings`: `mode`, `audio_only`, `verification_confirmed_at` and `connection_json` (the stats payload, no
    IPs).
  - The chunk endpoint gains optional `source`, `offset_uncertainty_ms` and `rtt_ms` query parameters; LAN defaults are
    unchanged.
  - The recordings list adds `source`. All schema changes are additive.

### 7.1 Patient consent text

> This call is recorded so your clinician can review it. Your voice is recorded **on this device** and sent, encrypted,
> **directly to your clinician's computer**. It is not stored on any server. Recordings waiting to be sent are kept
> briefly on this device and deleted as soon as the clinician's computer confirms it received them. Recording cannot
> start until you agree. You can leave the call at any time.

---

## 8. Phases

| Phase | Content | Main files |
|---|---|---|
| 0 | This plan and the `CLAUDE.md` rules. **Stop for approval.** | `docs/INTERNET_MEETINGS_PLAN.md`, `CLAUDE.md` |
| 1 | Public app, gateway, hub refactor, patient bundle, security headers, rate limits, origin normalisation, internet-mode checks on the clinician API, launcher, `.env` staging guard | `services/meeting_public/{__init__,app,gateway,limits,serve}.py`, `services/api/meeting_hub.py`, `meeting_routes.py`, `main.py` (middleware), `apps/web/{meeting.html,src/meeting-main.tsx,vite.config.ts,package.json scripts}`, `scripts/run_internet.py`, `.env.example`, tests |
| 2 | ICE issuance (Cloudflare, Metered, STUN fallback), TTL clamp, revocation, audio-only option, connection type, usage stats and monthly total | `services/api/turn.py`, migration `0009`, routes, `MeetingRoom.tsx`, `MeetingEntry.tsx`, tests |
| 3 | Data channel, protocol module, chat and media moved off signaling, verification code and its audit | `apps/web/src/features/meetings/{protocol,verify}.ts`, `MeetingRoom.tsx`, `meeting_routes.py`, tests |
| 4 | Patient-side recording, encrypted outbox, chunk transfer with backpressure, reassembly, clock sync, fallback and source labels, consent text | `apps/web/src/features/meetings/{patientRecorder,transfer,outbox}.ts`, chunk endpoint, migration columns, session sources UI, tests |
| 5 | Playwright internet mode, docs, manual checklist, README/STATUS updates | `apps/web/e2e/meeting-internet.spec.ts`, `playwright.internet.config.ts`, `docs/INTERNET_MEETINGS.md`, `docs/INTERNET_MEETINGS_TEST_CHECKLIST.md` |

Each phase ends with backend tests, the frontend build and the meeting Playwright suite, then a commit (no co-author
trailer), a progress-log entry here, and a stop.

---

## 9. Test plan

- **Phase 1:**
  - public routes allow-list: every clinician path (`/api/v1/patients`, `/sessions`, `…/transcript`, `/note-draft`,
    `/research/*`, `/progress/*`, `/patients/*/progress`, recording uploads, `/docs`, `/openapi.json`) gives 404 on the
    public app;
  - the host token is rejected on every public route;
  - expired, ended and invalid tokens give 410/403;
  - rate limits give 429 after N requests;
  - body over the size limit gives 413;
  - a bad `Origin` gives 403 on consent and the WebSocket;
  - CSP and other headers are present, and the built `meeting.html` has no inline script and no external URL;
  - the import-graph test (§2.3);
  - the clinician API with `Host: nachovarga.tail5ed395.ts.net` or `X-Forwarded-For` gives 403 in internet mode, on HTTP
    and on the WebSocket;
  - origin normalisation, and refusing a non-https origin;
  - the launcher refuses non-loopback binds;
  - `.env` is ignored, untracked and not staged;
  - patient and clinician sockets meet in the same hub room;
  - the LAN tests are unchanged.
- **Phase 2:** credentials are mocked with `httpx.MockTransport`:
  - port-53 URLs are dropped;
  - the TTL is clamped to the remaining lifetime;
  - nothing is cached across meetings;
  - the failure path returns STUN-only with a warning;
  - missing configuration gives STUN-only;
  - the API token never appears in a response or the logs (caplog);
  - revocation runs on end;
  - stats are stored without IPs, and the monthly totals are correct.
- **Phase 3:**
  - the protocol encodes and decodes and rejects oversize and unknown messages;
  - code derivation is identical on both sides, order-independent, and changes with any fingerprint (TypeScript tests
    run through a small node harness, plus a Python mirror for a fixed vector);
  - signaling drops `chat`;
  - the verification audit event is written.
- **Phase 4:**
  - chunking and reassembly with lost, duplicated and reordered parts;
  - a hash mismatch is rejected;
  - duplicate chunk ids are idempotent;
  - deletion only after the ack;
  - the fallback path uploads `network_received` only for uncovered windows;
  - no recording before consent;
  - the new chunk parameters are validated.
- **Phase 5 (Playwright, internet mode):**
  - two contexts with fake media, the public app on its port, the clinician on loopback, a mocked ICE configuration;
  - connect, the verification code appears and matches, chat over the data channel (with an assertion that no chat frame
    crossed the signaling socket, from a socket spy in the test page);
  - record, patient chunks arrive over the data channel, upload with `source=patient_device`, then end the call.

---

## 10. Manual checks for the user (Phase 1, before Phase 2 work depends on them)

- Run `tailscale funnel --bg 8443` and `tailscale funnel status`.
- Open `https://<origin>/healthz` from a phone on mobile data.
- With the launcher running:
  - the invitation page loads;
  - the WebSocket connects through Funnel (confirms the undocumented WebSocket support);
  - the launcher's public-route check passes.
- The launcher also logs, in a diagnostic mode, **which forwarding headers Funnel actually adds** (names only, no
  values). That decides whether the clinician-API forwarding check can key on a header as well as on `Host`.
- `tailscale funnel reset` afterwards.

---

## 11. Open questions (answers needed before Phase 1)

1. **Q1 – Dependency:** move `httpx` from the `dev` extra to core dependencies for TURN credential requests (it is
   already installed). The alternative is stdlib `urllib`, which avoids a dependency change; the task asked for `httpx`.
   **Recommendation: approve.**
2. **Q2 – One process for both apps** (§2.1), needed for shared rooms. The alternative, a separate public process,
   would need an authenticated internal channel. **Recommendation: one process.**
3. **Q3 – Patient-only bundle** (`meeting.html` entry, `dist-meeting/`) instead of serving the full app build publicly.
   **Recommendation: yes.**
4. **Q4 – Fallback rule:** upload a network-received fallback chunk only when patient-device chunks cover less than 50%
   of its window. The alternative is to upload both and mark the source, which duplicates transcript text.
5. **Q5 – Patient outbox key:** memory-only, so a reload makes pending chunks unrecoverable and the fallback covers that
   time. The alternative is keeping the key in `sessionStorage`, which survives a reload but is weaker.
   **Recommendation: memory-only.**
6. **Q6 – Patient microphone processing** for ASR: echo cancellation on, noise suppression off, auto gain off. The call
   would sound slightly noisier than today.
7. **Q7 – LAN mode** gets the data channel, verification code and patient-side recording too, with the same code path.
   The alternative is keeping LAN exactly as today and enabling the new features only in internet mode.
   **Recommendation: same path for both**, because LAN audio quality also improves. Either way `run_lan.py` and its
   tests keep passing.
8. **Q8 – Revoke Cloudflare credentials** when the meeting ends. **Recommendation: yes, best effort.**
9. **Q9 – Where to show monthly usage:** there is no meeting settings page today. Proposed: a line in the session's
   "Meet online" panel and a small `/workspace/meetings` page. The cost estimate uses $0.05/GB.
10. **Q10 – The pre-existing meeting e2e failure:** chunk gaps of 16 ms and 8 ms against a 1 ms limit; it reproduces
    when run alone. **Recommendation:** investigate and fix it at the start of Phase 1, because patient-side chunk timing
    builds on the same worklet. Relax the tolerance only if the gap turns out to be a measurement artifact, and say why.

---

## 12. Progress log

### Phase 0 — 8 November 2026
- **Done:**
  - read the meeting backend, middleware, LAN launcher, frontend, worklet and tests;
  - recorded the baseline (§1);
  - checked the Cloudflare TURN and Tailscale Funnel docs (§1.2);
  - wrote this plan and the `CLAUDE.md` rules.

  No code changed.
- **Baseline:** backend 302 passed; build passes; meeting Playwright 3 passed, 1 failed (pre-existing chunk-gap
  assertion; it also fails when run alone, with an 8 ms gap).
- **Next:** wait for approval and answers to §11, then Phase 1.
