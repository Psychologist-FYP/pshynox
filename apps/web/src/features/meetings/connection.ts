// Connection type and byte counts from WebRTC getStats(). Only candidate TYPES and counters are kept: no IP
// addresses, ports or candidate strings leave this function.
export type ConnectionInfo = { relayed: boolean; local_candidate_type: string; remote_candidate_type: string; bytes_sent: number; bytes_received: number };
const TYPES = new Set(["host", "srflx", "prflx", "relay"]);

export async function selectedPair(pc: RTCPeerConnection): Promise<ConnectionInfo | null> {
  const stats = await pc.getStats();
  let pairId: string | undefined;
  stats.forEach(r => { if (r.type === "transport" && r.selectedCandidatePairId) pairId = r.selectedCandidatePairId; });
  let pair: any = pairId ? stats.get(pairId) : undefined;
  if (!pair) stats.forEach(r => { if (!pair && r.type === "candidate-pair" && r.nominated && r.state === "succeeded") pair = r; });
  if (!pair) return null;
  const kind = (id: string) => { const c = stats.get(id) as { candidateType?: string } | undefined; return c?.candidateType && TYPES.has(c.candidateType) ? c.candidateType : "unknown"; };
  const local = kind(pair.localCandidateId), remote = kind(pair.remoteCandidateId);
  return { relayed: local === "relay" || remote === "relay", local_candidate_type: local, remote_candidate_type: remote,
    bytes_sent: Number(pair.bytesSent) || 0, bytes_received: Number(pair.bytesReceived) || 0 };
}
