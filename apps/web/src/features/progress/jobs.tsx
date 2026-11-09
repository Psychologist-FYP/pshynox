import { useEffect, useRef, useState } from "react";
import { progressApi, type ProgressJob } from "./api";

export type JobHandle = { job: ProgressJob | null; running: boolean; error: string; start: (launch: () => Promise<{ job_id: string }>) => Promise<void>; cancel: () => void; retry: () => void; attach: (id: string) => void };

/** Poll a local progress job; refresh dashboard data when it settles. */
export function useProgressJob(onSettled: () => void): JobHandle {
  const [job, setJob] = useState<ProgressJob | null>(null);
  const [error, setError] = useState("");
  const timer = useRef<number | null>(null);
  const settled = useRef(onSettled);
  settled.current = onSettled;
  const stop = () => { if (timer.current) window.clearTimeout(timer.current); timer.current = null; };
  const poll = (id: string) => {
    stop();
    progressApi.job(id).then(j => {
      setJob(j);
      if (j.status === "running") timer.current = window.setTimeout(() => poll(id), 1500);
      else settled.current();
    }).catch(e => setError(e.message));
  };
  useEffect(() => stop, []);
  return {
    job, error, running: job?.status === "running",
    start: async launch => { setError(""); try { const { job_id } = await launch(); poll(job_id); } catch (e) { setError(e instanceof Error ? e.message : "Could not start analysis."); } },
    cancel: () => { if (job) void progressApi.cancelJob(job.id); },
    retry: () => { if (job) void progressApi.retryJob(job.id).then(r => poll(r.job_id)).catch(e => setError(e.message)); },
    attach: id => poll(id),
  };
}

export function JobStatus({ job }: { job: JobHandle }) {
  if (job.error) return <p className="form-error" role="alert">{job.error}</p>;
  if (!job.job) return null;
  const j = job.job;
  return <div className="job-status" role="status">
    <span>Local analysis: <strong>{j.status}</strong>{j.status === "running" ? ` · ${j.stage}` : ""}</span>
    {j.status === "running" && <button type="button" className="text-button" onClick={job.cancel}>Cancel</button>}
    {["failed", "cancelled", "interrupted"].includes(j.status) && <button type="button" className="text-button" onClick={job.retry}>Retry / resume</button>}
    {j.error && <p className="form-error">{j.error}</p>}
    {!!j.limitations?.length && <details><summary>Limitations ({j.limitations.length})</summary><ul>{j.limitations.map((l, i) => <li key={i}>{l}</li>)}</ul></details>}
  </div>;
}
