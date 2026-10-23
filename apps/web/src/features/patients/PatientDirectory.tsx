import { createContext, useContext, useEffect, useState, type ReactNode } from "react";
import { api, type Patient } from "../../lib/api";

const Directory = createContext<{ patients: Patient[]; loading: boolean; error: string; reload: () => Promise<void> }>({ patients: [], loading: true, error: "", reload: async () => {} });
export const usePatients = () => useContext(Directory);
export function PatientDirectory({ children }: { children: ReactNode }) {
  const [patients, setPatients] = useState<Patient[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  async function reload() {
    setError("");
    try { setPatients(await api.patients()); }
    catch (e) { setError(e instanceof Error ? e.message : "Could not load patients."); }
    finally { setLoading(false); }
  }
  useEffect(() => { void reload(); }, []);
  return <Directory.Provider value={{ patients, loading, error, reload }}>{children}</Directory.Provider>;
}
export function patientAge(patient: Patient): string {
  if (!patient.date_of_birth) return patient.age === null ? "Age not recorded" : `${patient.age} years at intake`;
  const [year, month, day] = patient.date_of_birth.split("-").map(Number);
  const now = new Date();
  const age = now.getFullYear() - year - (now.getMonth() + 1 < month || (now.getMonth() + 1 === month && now.getDate() < day) ? 1 : 0);
  return `${age} years`;
}
export function initials(name: string) { return name.trim().split(/\s+/).slice(0, 2).map(part => part[0]).join("").toUpperCase(); }
