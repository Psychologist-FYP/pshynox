import { useState, type FormEvent } from "react";
import { api, type Patient, type PatientInput } from "../../lib/api";

export const intakeGroups: { title: string; hint: string; fields: { key: keyof PatientInput; label: string; type?: string; wide?: boolean }[] }[] = [
  { title: "Personal details", hint: "Start with the essentials. Only the patient's name is required.", fields: [
    { key: "name", label: "Full name" }, { key: "preferred_name", label: "Preferred name" },
    { key: "date_of_birth", label: "Date of birth", type: "date" }, { key: "age", label: "Age (if date of birth is unknown)", type: "number" },
    { key: "sex", label: "Sex", type: "sex" }, { key: "gender", label: "Gender identity" },
    { key: "pronouns", label: "Pronouns" }, { key: "language", label: "Preferred language" }] },
  { title: "Contact & support", hint: "Contact details and a person to reach when needed.", fields: [
    { key: "phone", label: "Phone number", type: "tel" }, { key: "email", label: "Email address", type: "email" },
    { key: "address", label: "Address", wide: true }, { key: "emergency_contact", label: "Emergency contact name" },
    { key: "emergency_relationship", label: "Relationship to patient" }, { key: "emergency_phone", label: "Emergency contact phone", type: "tel" },
    { key: "guardian", label: "Parent / guardian (if applicable)" }] },
  { title: "Background", hint: "A little context about the patient's everyday life.", fields: [
    { key: "occupation", label: "Occupation" }, { key: "education", label: "Education" },
    { key: "marital_status", label: "Relationship / marital status" }, { key: "referred_by", label: "Referral source" }] },
  { title: "Intake & history", hint: "Record what has been shared. You can return to these fields later.", fields: [
    { key: "presenting_concerns", label: "Reason for seeking help / presenting concerns", type: "textarea", wide: true },
    { key: "previous_care", label: "Previous psychological / psychiatric care", type: "textarea" },
    { key: "medical_history", label: "Relevant medical history", type: "textarea" },
    { key: "medications", label: "Current medications", type: "textarea" }, { key: "allergies", label: "Allergies", type: "textarea" },
    { key: "family_social_history", label: "Family, social context & support", type: "textarea" },
    { key: "substance_use", label: "Substance use history", type: "textarea" },
    { key: "goals", label: "Patient's goals / expectations", type: "textarea" },
    { key: "additional_notes", label: "Additional intake notes", type: "textarea" }] },
];

export function PatientForm({ patient, onSaved }: { patient?: Patient; onSaved: (patient: Patient) => void }) {
  const [values, setValues] = useState<PatientInput>(() => Object.fromEntries(intakeGroups.flatMap(g => g.fields).map(f => [f.key, patient?.[f.key] ?? (f.key === "age" || f.key === "date_of_birth" ? null : "")])) as PatientInput);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  function change(key: keyof PatientInput, value: string) {
    setValues(old => ({ ...old, [key]: key === "age" ? (value === "" ? null : Number(value)) : key === "date_of_birth" ? value || null : value,
      ...(key === "date_of_birth" && value ? { age: null } : {}) }));
  }
  async function submit(event: FormEvent) {
    event.preventDefault(); setSaving(true); setError("");
    try { onSaved(patient ? await api.savePatient(patient.id, values) : await api.createPatient(values)); }
    catch (e) { setError(e instanceof Error ? e.message : "Could not save patient."); setSaving(false); }
  }
  return <form className="patient-intake" onSubmit={submit}>
    {intakeGroups.map((group, index) => <fieldset className="panel intake-section" key={group.title}><legend><span>{String(index + 1).padStart(2, "0")}</span> {group.title}</legend><p>{group.hint}</p><div className="intake-grid">
      {group.fields.map(f => <label key={f.key} className={f.wide ? "wide" : ""} htmlFor={`patient-${f.key}`}><span>{f.label}{f.key === "name" && <span aria-hidden="true"> *</span>}</span>
        {f.type === "textarea" ? <textarea id={`patient-${f.key}`} rows={3} maxLength={f.key === "allergies" ? 2000 : ["medications", "substance_use", "goals"].includes(f.key) ? 3000 : 5000} value={String(values[f.key] ?? "")} onChange={e => change(f.key, e.target.value)} /> : f.type === "sex" ? <select aria-label={f.label} id={`patient-${f.key}`} value={values.sex} onChange={e => change(f.key, e.target.value)}><option value="">Not recorded</option>{["Female", "Male", "Intersex", "Prefer not to say"].map(value => <option key={value}>{value}</option>)}</select> :
          <input id={`patient-${f.key}`} type={f.type ?? "text"} required={f.key === "name"} autoFocus={f.key === "name"} disabled={f.key === "age" && !!values.date_of_birth} min={f.key === "age" ? 0 : undefined} max={f.key === "age" ? 120 : f.type === "date" ? new Date().toLocaleDateString("en-CA") : undefined} maxLength={f.key === "address" ? 1000 : f.key === "email" ? 254 : ["phone", "emergency_phone"].includes(f.key) ? 80 : ["gender", "pronouns", "emergency_relationship", "language", "marital_status"].includes(f.key) ? 100 : 200} value={values[f.key] ?? ""} onChange={e => change(f.key, e.target.value)} />}
      </label>)}
    </div></fieldset>)}
    <div className="patient-form-actions"><span>{error ? <span className="form-error" role="alert">{error}</span> : "You can update these details at any time."}</span><div><a className="button button-secondary" href={patient ? `/workspace/patients/${patient.id}` : "/workspace"}>Cancel</a><button className="button button-primary" disabled={saving || !values.name.trim()}>{saving ? "Saving…" : patient ? "Save changes" : "Create patient"}</button></div></div>
  </form>;
}
