import { test } from "node:test";
import assert from "node:assert/strict";
import { fingerprints, formatCode, verificationCode } from "../src/features/meetings/verify.ts";

const fp = byte => Array(32).fill(byte).join(":");
const sdp = (hex, extra = "") => `v=0\r\no=- 1 2 IN IP4 127.0.0.1\r\na=fingerprint:sha-256 ${hex}\r\n${extra}m=audio 9 UDP/TLS/RTP/SAVPF 111\r\na=fingerprint:SHA-256 ${hex}\r\n`;

test("fingerprints are normalised and de-duplicated", () => {
  assert.deepEqual(fingerprints(sdp(fp("ab"))), [`sha-256 ${fp("AB")}`]);
});

test("both peers derive the same code, independent of order; matches the Python reference vector", async () => {
  const a = sdp(fp("AB")), b = sdp(fp("CD"));
  const clinician = await verificationCode(a, b), patient = await verificationCode(b, a);
  assert.equal(clinician, patient);
  assert.equal(clinician, "459652");                 // computed independently with Python hashlib
  assert.equal(formatCode(clinician), "459 652");
});

test("a swapped key changes the code; missing fingerprints give no code", async () => {
  const real = await verificationCode(sdp(fp("AB")), sdp(fp("CD")));
  assert.notEqual(await verificationCode(sdp(fp("AB")), sdp(fp("CE"))), real);
  assert.equal(await verificationCode("v=0\r\n", sdp(fp("CD"))), null);
});
