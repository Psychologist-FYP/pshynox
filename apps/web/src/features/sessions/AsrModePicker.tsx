import { useEffect, useState } from "react";
import { api, type AsrMode, type AsrProducedBy, type AsrStatus, type SpeechJob } from "../../lib/api";

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
    {info.notices.length ? info.notices.join(" · ") : `Uses ${info.model_label || info.model} (${info.language})${info.device && info.device !== "auto" ? ` · ${DEVICE_LABELS[info.device]}` : ""}${info.non_commercial ? " · non-commercial licence" : ""}`}
  </span>;
}

const DEVICE_LABELS = { cuda: "GPU (CUDA)", vulkan: "GPU (Vulkan)", cpu: "CPU" } as const;

/** "Urdu Sukhan ASR v1 (q8_0) · Urdu or mixed Urdu–English (ur) · whisper.cpp · GPU (Vulkan, NVIDIA GeForce GTX 1650) · VAD on" */
export function describeProducedBy(p: AsrProducedBy): string {
  const device = p.device === "gpu" ? `GPU (${[p.gpu_backend === "cuda" ? "CUDA" : p.gpu_backend === "vulkan" ? "Vulkan" : null, p.gpu_device].filter(Boolean).join(", ") || "GPU"})` : p.device === "cpu" ? "CPU" : null;
  return [p.asr_model_label || p.asr_model || "unknown model", p.asr_mode ? `${ASR_MODE_LABELS[p.asr_mode]}${p.language ? ` (${p.language})` : ""}` : null,
    p.asr_engine, device, p.vad === true ? "VAD on" : p.vad === false ? "VAD off" : null, p.asr_fallback ? "fallback model" : null].filter(Boolean).join(" · ");
}

/** The model and mode behind one recording's transcript, with its licence when it is non-commercial. */
export function ProducedBy({ value }: { value: AsrProducedBy }) {
  return <span className="small-note recording-asr">Transcribed with {describeProducedBy(value)}{value.model_non_commercial ? <span className="asr-licence"> · {value.model_licence?.replaceAll("-", " ") ?? "non-commercial"}: non-commercial use only</span> : null}</span>;
}

/** Above the transcript: which model(s) and mode(s) produced it (one line per distinct combination). */
export function TranscriptProvenance({ sessionId, refreshKey }: { sessionId: string; refreshKey: string }) {
  const [jobs, setJobs] = useState<SpeechJob[]>([]);
  useEffect(() => {
    let live = true;
    api.speechJobs(sessionId).then(j => { if (live) setJobs(j); }).catch(() => undefined);
    return () => { live = false; };
  }, [sessionId, refreshKey]);
  const seen = new Map<string, AsrProducedBy>();
  for (const j of jobs) if (j.produced_by) seen.set(describeProducedBy(j.produced_by), j.produced_by);
  if (!seen.size) return null;
  return <div className="transcript-provenance">{[...seen.values()].map(p => <ProducedBy key={describeProducedBy(p)} value={p} />)}</div>;
}

export function AsrModePicker({ value, onChange, disabled, label = "Speech recognition language", status }: {
  value: AsrMode; onChange: (mode: AsrMode) => void; disabled?: boolean; label?: string; status: AsrStatus | null;
}) {
  return <div className="asr-mode-picker"><label>{label} <select aria-label={label} value={value} disabled={disabled} onChange={e => onChange(e.target.value as AsrMode)}>
    {(Object.keys(ASR_MODE_LABELS) as AsrMode[]).map(m => <option key={m} value={m}>{ASR_MODE_LABELS[m]}</option>)}
  </select></label> <AsrModeNotice mode={value} status={status} /></div>;
}
