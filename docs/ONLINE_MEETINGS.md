# Online meetings on the same Wi-Fi

This is a two-person academic demo: one clinician on the hosting laptop and one
patient on another device on the same Wi-Fi. An invitation does not expose the
patient directory, notes, or other sessions. More than two participants per room
are not supported. Use fictional conversations.

## Start the LAN demo

Requirements: project Python environment, Node/npm, OpenSSL, Google Chrome or a
current browser supporting WebRTC and AudioWorklet, and FFmpeg/Whisper configured
as described in `services/speech/README.md` for local transcription.

Install frontend dependencies once:

```bash
cd apps/web
npm ci
cd ../..
```

Stop previous frontend/backend development servers. From the repository root:

```bash
source .venv/bin/activate
python scripts/run_lan.py
```

The launcher detects the laptop's IPv4 route, creates local HTTPS certificates in
ignored `data/lan/`, starts the API on loopback, starts Vite on all LAN interfaces,
and prints the doctor address and patient invitation origin. It loads an existing
root `.env` into the API. It does not alter firewall or certificate trust settings.
It does not require a hosted meeting provider or an internet connection after setup.

If auto-detection picks a VPN or Ethernet address, specify the Wi-Fi address:

```bash
python scripts/run_lan.py --ip 192.168.1.20
```

Replace the example address with the actual hosting laptop's Wi-Fi IP. Choose
alternate ports with `--api-port 8001 --web-port 5174` if necessary. Use one API
process; multiple workers do not share the live signaling room registry. The
launcher intentionally does not use reload, which would interrupt active calls.

## Trust the certificate once on each device

Microphone/camera and AudioWorklet require a secure browser context. A plain HTTP
LAN IP address does not meet this requirement. The launcher creates a local CA
certificate at `data/lan/ca.pem`; install this public certificate as trusted on the
hosting laptop and patient device. Then reopen the browser.

- Desktop: import `ca.pem` through the browser/OS certificate authority settings.
  Some browsers use the OS store; Firefox may use its own Authorities store.
- Android: install `ca.pem` as a CA certificate in device security settings; device
  policies may restrict this. Use Chrome after installation.
- iPhone/iPad: install the certificate profile, then enable trust for the Pshynox
  Wi-Fi Demo CA in Settings > General > About > Certificate Trust Settings.

Transfer only `ca.pem`. Never transfer `ca-key.pem` or `meeting-key.pem`. Trust
this CA only on devices you control for the demo, and remove it when finished.
If the Wi-Fi IP changes, rerun the launcher: the server certificate is regenerated
for the new address while retaining the CA. Old invitations may contain the old
address; close that invitation and create a new meeting.

For certificate preparation without starting servers:

```bash
python scripts/run_lan.py --ip 192.168.1.20 --cert-only
```

## Make the call

1. Doctor opens **https://localhost:5173** on the hosting laptop. Keep using
   localhost for the clinician workspace, not the laptop's LAN IP.
2. Open a patient, start/open a session, confirm recording consent, and choose
   **Start online meeting**.
3. Use **Copy link** in the invitation box. Send that patient invitation to the
   other device. Do not copy the clinician room's browser address.
4. Patient opens the invitation on the same Wi-Fi. Both participants confirm
   recording consent, check microphone/camera permissions, and click **Join meeting**.
5. Use headphones to prevent acoustic echo/cross-talk. Camera is optional; choose
   audio only before checking devices if no camera is available. Each participant
   can turn their camera on or off during the call, including after joining audio
   only. The other participant's camera works independently.
6. Keep both devices awake and the clinician tab open. After about a minute, two
   audio chunks should appear in the clinician's progress panel. Transcript text
   follows when the local worker finishes each job.
7. Doctor chooses **End meeting** and waits for final audio uploads. Return to the
   session to review both speaker tracks and populate patient excerpts into the note.

Invitation tokens authorize a specific role in a specific meeting and expire after
four hours. Ending the meeting closes admission. A second tab using the same role
is rejected; use the patient invitation or a separate browser context for testing.
If you lose the creating tab, close the previous invitation from the session page
and create another one. Do not close it while recording/uploading is in progress.

## Networking checks

- Allow inbound **TCP 5173** (or your chosen frontend port) on the laptop for the
  local/private network. Port 8000 stays bound to loopback and is not shared.
- WebRTC also needs direct UDP connectivity between the devices; browser traffic
  must not be blocked by the host firewall. Port 5173 alone is not the media path.
- Guest Wi-Fi/client isolation, some hotspots, and VPNs can prevent direct device
  connections even if both devices show the same Wi-Fi name. Disable client
  isolation/use a normal trusted Wi-Fi network for the demo.
- LAN calls use host ICE candidates with no external STUN/TURN service by default.
  Different-network calls are outside this LAN setup; configure a suitable TURN
  relay before expecting reliable operation across networks.
- If localhost links are generated, use the launcher or set `MEETING_PUBLIC_ORIGIN`
  in the API environment before creating a new room.

## Capture and persistence

WebRTC sends camera/microphone media directly between browsers. The clinician's
browser captures its local microphone and the received patient audio as separate
mono streams. AudioWorklet continuously captures PCM and emits standalone WAV
files after 60 seconds of samples; stopping flushes the final partial file. There
is no intentional overlap or stop/start gap. Both tracks use the same audio clock.
The patient track reflects network arrival/playout timing, not a perfectly
synchronized remote hardware clock. Packet loss, echo, and interruptions still
need review. A word straddling an ASR chunk boundary may be misrecognized.

Pending chunks are stored in IndexedDB on the clinician's browser. Uploads are
idempotent, so retrying after a lost response does not duplicate audio or jobs.
The backend saves a recording, speaker role, meeting offset and ASR job together.
The local worker processes chunks sequentially while capture continues; processing
may fall behind if inference is slower than two streams of incoming audio.

Uploaded chunks survive API restart. Unuploaded IndexedDB chunks can be retried
from the same browser/origin and meeting tab within the invitation lifetime. An
abrupt tab/process termination can lose the current partial minute. This is not a
guarantee of lossless capture during browser/OS suspension. Use End meeting and
wait for upload completion. No video or chat history is persisted.

Transcript timestamps are meeting-relative for meeting chunks; audio playback
subtracts each chunk's offset. CLIENT/CLINICIAN labels come from the assigned
microphone track, not voice recognition. The existing source references, revision
tracking, manual correction, note population and review gates remain in use.

## Configuration for manual startup

- `MEETING_PUBLIC_ORIGIN=https://192.168.1.20:5173` (API)
- `MEETING_ICE_SERVERS=[]` (API; JSON RTCIceServer list, empty for direct LAN)
- `VITE_HTTPS_CERT=/absolute/path/data/lan/meeting.pem` (frontend)
- `VITE_HTTPS_KEY=/absolute/path/data/lan/meeting-key.pem` (frontend)
- `VITE_API_TARGET=http://127.0.0.1:8000` (frontend)

The Vite proxy overwrites `X-Pshynox-Client-IP` from the actual socket address.
The API trusts it only from loopback, and rejects LAN access to non-meeting APIs.
Do not expose the unprotected clinician API behind another proxy that replaces
all clients with loopback without applying equivalent access controls. This is a
LAN development workflow, not an authenticated production telehealth service.

## Verification

```bash
.venv/bin/python -m pytest
cd apps/web
npm run build
npm run test:meeting
```

Browser tests use installed Google Chrome, two independent browser contexts,
synthetic media, a temporary database/audio folder, and isolated ports 8011/5179.
They verify received video frames with either or both cameras initially off,
turning cameras on after joining, and independent camera toggles.
They never use the developer's patient database or microphone. The minute-long
recording test intentionally takes over a minute. ASR is disabled in browser tests;
backend tests validate transcript role/timestamp conversion with a fake ASR adapter.
Physical Wi-Fi devices and browser certificate installation still need a manual
acceptance call on the intended network.
