// Patient-only entry for the public meeting server (internet mode). It bundles the meeting room and nothing from
// the clinician workspace.
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { MeetingRoom } from "./features/meetings/MeetingRoom";
import "./styles.css";
import "./katz-theme.css";

const meetingId = /^\/meeting\/(meeting-[a-f0-9]{32})\/?$/.exec(location.pathname)?.[1];
createRoot(document.getElementById("root")!).render(<StrictMode>{meetingId ? <MeetingRoom id={meetingId} /> : <p className="meeting-alert">This invitation link is incomplete. Ask your clinician for a new one.</p>}</StrictMode>);
