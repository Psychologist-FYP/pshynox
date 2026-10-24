import { StrictMode, useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import { LandingPage } from "./features/landing/LandingPage";
import { SessionDetailPage } from "./features/sessions/SessionDetailPage";
import { WorkspacePage } from "./features/sessions/WorkspacePage";
import { PatientPage } from "./features/patients/PatientPage";
import { PatientDirectory } from "./features/patients/PatientDirectory";
import { WorkspaceShell } from "./components/layout/AppShell";
import { api, type Health, type Session } from "./lib/api";
import { MeetingRoom } from "./features/meetings/MeetingRoom";
import "./styles.css";
import "./katz-theme.css";

function SessionRoute({ id, health }: { id: string; health: Health | null }) {
  const [session, setSession] = useState<Session | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  useEffect(() => {
    const controller = new AbortController();
    api.session(id, controller.signal).then(setSession).catch(e => { if (e.name !== "AbortError") setError(e.message); }).finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [id]);
  if (loading) return <WorkspaceShell health={health} active="Session"><main className="section-wrap"><div className="state-panel" role="status">Loading session…</div></main></WorkspaceShell>;
  return <SessionDetailPage health={health} session={session} error={error} />;
}

function App() {
  const [health, setHealth] = useState<Health | null>(null);
  const [healthError, setHealthError] = useState<string | null>(null);
  const [path, setPath] = useState(window.location.pathname);
  useEffect(() => {
    const controller = new AbortController();
    api.health(controller.signal).then(setHealth).catch(e => { if (e.name !== "AbortError") setHealthError(e.message); });
    return () => controller.abort();
  }, []);
  useEffect(() => {
    const pop = () => setPath(window.location.pathname);
    window.addEventListener("popstate", pop);
    return () => window.removeEventListener("popstate", pop);
  }, []);
  if (!path.startsWith("/workspace")) return <LandingPage health={health} error={healthError} />;
  const parts = path.split("/").filter(Boolean);
  let page;
  if (parts[1] === "sessions" && parts[2]) page = <SessionRoute key={parts[2]} id={parts[2]} health={health} />;
  else if (parts[1] === "patients" && parts[2] === "new") page = <PatientPage health={health} />;
  else if (parts[1] === "patients" && parts[2]) page = <PatientPage key={path} health={health} patientId={parts[2]} editing={parts[3] === "edit"} />;
  else page = <WorkspacePage health={health} />;
  return <PatientDirectory>{page}</PatientDirectory>;
}
const meetingId = /^\/meeting\/(meeting-[a-f0-9]{32})\/?$/.exec(location.pathname)?.[1];
// Invitation pages never mount the patient directory or fetch clinical records.
createRoot(document.getElementById("root")!).render(<StrictMode>{meetingId ? <MeetingRoom id={meetingId} /> : <App />}</StrictMode>);
