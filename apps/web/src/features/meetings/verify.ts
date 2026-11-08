// Short verification code from both peers' DTLS certificate fingerprints (docs/INTERNET_MEETINGS_PLAN.md §6).
// Both browsers compute the same 6 digits; if anyone on the signaling path swapped keys, the codes differ.
// Pure functions: no DOM, so node --test can run the unit tests.

/** All a=fingerprint values in an SDP, normalised (algorithm lower-case, hex upper-case), unique. */
export function fingerprints(sdp: string): string[] {
  const out = new Set<string>();
  for (const line of sdp.split(/\r?\n/)) {
    const match = /^a=fingerprint:(\S+)\s+([0-9A-Fa-f:]+)\s*$/.exec(line.trim());
    if (match) out.add(`${match[1].toLowerCase()} ${match[2].toUpperCase()}`);
  }
  return [...out];
}

/** 6-digit code from the sorted fingerprints of both descriptions. Order of local/remote does not matter. */
export async function verificationCode(localSdp: string, remoteSdp: string): Promise<string | null> {
  const local = fingerprints(localSdp), remote = fingerprints(remoteSdp);
  if (!local.length || !remote.length) return null;
  const input = new TextEncoder().encode([...new Set([...local, ...remote])].sort().join("\n"));
  const digest = new DataView(await crypto.subtle.digest("SHA-256", input));
  return String(digest.getUint32(0) % 1_000_000).padStart(6, "0");
}

export const formatCode = (code: string) => `${code.slice(0, 3)} ${code.slice(3)}`;
