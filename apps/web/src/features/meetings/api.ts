export type MeetingInfo = {
  id: string; role: "CLINICIAN" | "CLIENT"; status: string; expires_ms: number;
  session_id?: string; ice_servers: RTCIceServer[];
  jobs?: { id: string; status: string; error_message: string | null }[];
};
export async function meetingRequest<T>(path: string, token = "", init: RequestInit = {}): Promise<T> {
  const response = await fetch(`/api/v1/${path}`, { ...init, headers: {
    "Content-Type": "application/json", ...(token ? { Authorization: `Bearer ${token}` } : {}), ...init.headers,
  }});
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(body.error?.message || body.detail?.message || `Meeting request failed (${response.status}).`);
  }
  return response.json() as Promise<T>;
}
export function tokenFor(id: string) {
  const hash = new URLSearchParams(location.hash.slice(1));
  const token = hash.get("token");
  if (token) {
    sessionStorage.setItem(`meeting-token-${id}`, token);
    history.replaceState(null, "", location.pathname);
  }
  return token || sessionStorage.getItem(`meeting-token-${id}`) || "";
}
