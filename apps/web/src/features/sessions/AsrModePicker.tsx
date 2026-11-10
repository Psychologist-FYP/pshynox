import { useEffect, useState } from "react";
import { api, type AsrMode, type AsrStatus } from "../../lib/api";

export const ASR_MODE_LABELS: Record<AsrMode, string> = { english: "English", urdu_mixed: "Urdu or mixed Urdu–English" };

let cached: Promise<AsrStatus> | null = null;
export function useAsrStatus() {
  const [status, setStatus] = useState<AsrStatus | null>(null);
  useEffect(() => {
    cached = cached ?? api.asrStatus().catch(e => { cached = null; throw e; });
    let live = true;
    cached.then(s => { if (live) setStatus(s); }).catch(() => undefined);
    return () => { live = false; };
  }, []);
  return status;
}

/** What a mode will run on this machine, including any fallback notice ("Urdu model not installed — using fallback"). */
export function AsrModeNotice({ mode, status }: { mode: AsrMode; status: AsrStatus | null }) {
  const info = status?.modes[mode];
  if (!info) return null;
  return <span className={info.fallback ? "asr-notice asr-notice-fallback" : "asr-notice"} role={info.fallback ? "alert" : undefined}>
    {info.notices.length ? info.notices.join(" · ") : `Uses ${info.engine === "parakeet-onnx" ? "Parakeet TDT 0.6B v2" : info.model} (${info.language})`}
  </span>;
}

export function AsrModePicker({ value, onChange, disabled, label = "Speech recognition language", status }: {
  value: AsrMode; onChange: (mode: AsrMode) => void; disabled?: boolean; label?: string; status: AsrStatus | null;
}) {
  return <div className="asr-mode-picker"><label>{label} <select aria-label={label} value={value} disabled={disabled} onChange={e => onChange(e.target.value as AsrMode)}>
    {(Object.keys(ASR_MODE_LABELS) as AsrMode[]).map(m => <option key={m} value={m}>{ASR_MODE_LABELS[m]}</option>)}
  </select></label> <AsrModeNotice mode={value} status={status} /></div>;
}
