import { StrictMode, useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import { LandingPage } from "./features/landing/LandingPage";
import { SessionDetailPage } from "./features/sessions/SessionDetailPage";
import { WorkspacePage } from "./features/sessions/WorkspacePage";
import { api, type Health, type Session } from "./lib/api";
import "./styles.css";

function App() {
  const [health, setHealth] = useState<Health | null>(null);
  const [healthError, setHealthError] = useState<string | null>(null);
  const [sessions, setSessions] = useState<Session[]>([]);
  const [sessionError, setSessionError] = useState<string | null>(null);
  const [path, setPath] = useState(window.location.pathname);

  useEffect(() => {
    const controller = new AbortController();
    api.health(controller.signal).then(setHealth).catch((reason: Error) => { if (reason.name !== "AbortError") setHealthError(reason.message); });
    return () => controller.abort();
  }, []);

  useEffect(() => {
    const onPopState = () => setPath(window.location.pathname);
    window.addEventListener("popstate", onPopState);
    return () => window.removeEventListener("popstate", onPopState);
  }, []);

  useEffect(() => {
    if (!path.startsWith("/workspace")) return;
    const controller = new AbortController();
    api.sessions(controller.signal).then(setSessions).catch((reason: Error) => { if (reason.name !== "AbortError") setSessionError(reason.message); });
    return () => controller.abort();
  }, [path]);

  useEffect(() => {
    const id = path.startsWith("/workspace/sessions/") ? path.split("/").pop() : undefined;
    if (!id || sessions.some((item) => item.id === id)) return;
    const controller = new AbortController();
    api.session(id, controller.signal).then((item) => setSessions((current) => [item, ...current])).catch((reason: Error) => {
      if (reason.name !== "AbortError") setSessionError(reason.message);
    });
    return () => controller.abort();
  }, [path, sessions]);

  const detailId = path.startsWith("/workspace/sessions/") ? path.split("/").pop() : undefined;
  const detailSession = sessions.find((item) => item.id === detailId) ?? null;
  if (detailId) return <SessionDetailPage health={health} session={detailSession} error={sessionError} />;
  if (path === "/workspace" || path === "/workspace/") return <WorkspacePage health={health} sessions={sessions} loading={Boolean(!sessions.length && !sessionError)} error={sessionError} onCreated={(created) => { setSessions((current) => [created, ...current]); window.history.pushState({}, "", `/workspace/sessions/${created.id}`); setPath(`/workspace/sessions/${created.id}`); }} />;
  return <LandingPage health={health} error={healthError} />;
}

createRoot(document.getElementById("root")!).render(<StrictMode><App /></StrictMode>);
