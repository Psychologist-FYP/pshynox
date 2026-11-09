"""Clinician review of the DSM registry: CSV and printable HTML export, and a CSV import that records decisions.

Decisions are stored in ``registry_verifications`` and bound to each entry's content SHA-256. The import never changes
ids or paraphrases: a "changed" decision is a request with the suggested wording in its comment.
"""
from __future__ import annotations

import csv
import html
import io
import uuid
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession

from services.api.db import RegistryVerificationRecord, utc_now

from .registry import DsmRegistry, content_sha256

COLUMNS = ["entity_kind", "id", "label", "text", "chapter", "icd10cm", "icd11", "content_sha256", "current_status",
           "decision", "verified_by", "verified_on", "comment"]
DECISIONS = {"verified", "changed", "pending"}
PARAPHRASE_NOTE = "Paraphrased by the project team from the DSM-5-TR criteria structure; not APA text."


def rule_summary(cond: dict) -> str:
    parts = []
    for e in cond["elements"]:
        name = e["id"]
        if e["type"] == "count":
            need = f"{e['min']} of {len(e['items'])}"
            extra = f", including {' or '.join(e['must_include_any'])}" if e.get("must_include_any") else ""
            only = f"; {e['min_if_only']['min']} if mood is only irritable" if e.get("min_if_only") else ""
            parts.append(f"{name}: at least {need}{extra}{only}")
        elif e["type"] == "duration":
            parts.append(f"{name}: duration ≥ {e['min_days']} days" + (" or hospitalised" if e.get("hospitalised_any") else ""))
        elif e["type"] in {"episode", "all_episodes"}:
            parts.append(f"{name}: needs {', '.join([e['condition']] if e.get('condition') else e['conditions'])} pattern")
        elif e["type"] == "never_episode":
            parts.append(f"{name}: never {', '.join(e['conditions'])}")
        elif e["type"] == "exclusion":
            parts.append(f"{name}: exclusion ({', '.join(e['features'])}); unassessed goes to information needed")
        elif e["type"] == "judgement":
            parts.append(f"{name}: clinical judgement; unassessed goes to information needed")
        elif e["type"] == "absence":
            parts.append(f"{name}: must not have {', '.join(e['items'])}")
        elif e["type"] == "subthreshold_course":
            parts.append(f"{name}: milder {' and '.join(e['groups'])} symptoms over ≥ {e['min_days']} days")
        elif e["type"] == "note":
            parts.append(f"{name}: note only")
        else:
            parts.append(f"{name}: present")
    return f"Core {cond['core']}. " + "; ".join(parts)


def effective(db: DbSession | None, reg: DsmRegistry) -> dict[tuple[str, str], dict]:
    """Current verification per entry: the latest decision for the current content hash, else the registry default."""
    out: dict[tuple[str, str], dict] = {}
    rows = list(db.scalars(select(RegistryVerificationRecord).where(RegistryVerificationRecord.registry_kind == "dsm")
                           .order_by(RegistryVerificationRecord.created_at))) if db is not None else []
    latest = {}
    for r in rows:
        latest[(r.entity_kind, r.entity_id, r.content_sha256)] = r
    for kind, entity_id, raw in reg.entries():
        sha = content_sha256(raw)
        default = raw.get("verification") or {"status": "pending", "verified_by": None, "verified_on": None, "comment": ""}
        rec = latest.get((kind, entity_id, sha))
        out[(kind, entity_id)] = ({"status": rec.status, "verified_by": rec.verified_by, "verified_on": rec.verified_on, "comment": rec.comment}
                                  if rec else dict(default)) | {"content_sha256": sha}
    return out


def rows(db: DbSession | None, reg: DsmRegistry) -> list[dict]:
    status = effective(db, reg)
    out = []
    for kind, entity_id, raw in reg.entries():
        v = status[(kind, entity_id)]
        if kind == "item":
            text, chapter, icd10, icd11, label = raw["paraphrase"], raw["chapter"], "", "", raw["label"]
        elif kind == "condition":
            text, chapter, icd10, icd11, label = rule_summary(raw), raw["chapter"], raw["icd10cm"], raw["icd11"], raw["label"]
        else:
            text, chapter, icd10, icd11, label = raw["definition"], "", "", "", raw["label"]
        out.append({"entity_kind": kind, "id": entity_id, "label": label, "text": text, "chapter": chapter, "icd10cm": icd10, "icd11": icd11,
                    "content_sha256": v["content_sha256"], "current_status": v["status"], "decision": "", "verified_by": v.get("verified_by") or "",
                    "verified_on": v.get("verified_on") or "", "comment": v.get("comment") or ""})
    return out


def export_csv(db: DbSession | None, reg: DsmRegistry) -> str:
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=COLUMNS)
    writer.writeheader()
    writer.writerows(rows(db, reg))
    return buffer.getvalue()


def export_html(db: DbSession | None, reg: DsmRegistry) -> str:
    data = rows(db, reg)
    by = {(r["entity_kind"], r["id"]): r for r in data}
    esc = html.escape
    blocks = []
    for cond in reg.conditions.conditions:
        c = by[("condition", cond.key)]
        item_ids = list(dict.fromkeys(i for e in cond.elements for i in ([e.item] if e.item else []) + e.items))
        lines = "".join(f"<tr><td>{esc(i)}</td><td>{esc(by[('item', i)]['text'])}</td><td>{esc(by[('item', i)]['current_status'])}</td>"
                        f"<td class=box>☐ verified ☐ changed</td><td class=comment></td></tr>" for i in item_ids)
        blocks.append(f"<section><h2>{esc(cond.label)} <small>{esc(cond.chapter)} · ICD-10-CM {esc(cond.icd10cm)} · ICD-11 {esc(cond.icd11)}</small></h2>"
                      f"<p class=rule><strong>Rule ({esc(c['current_status'])}):</strong> {esc(c['text'])} <span class=box>☐ verified ☐ changed</span></p>"
                      f"<table><tr><th>Id</th><th>Paraphrase</th><th>Status</th><th>Decision</th><th>Comment</th></tr>{lines}</table></section>")
    features = "".join(f"<tr><td>{esc(r['id'])}</td><td>{esc(r['label'])}: {esc(r['text'])}</td><td>{esc(r['current_status'])}</td>"
                       f"<td class=box>☐ verified ☐ changed</td><td class=comment></td></tr>" for r in data if r["entity_kind"] == "feature")
    return f"""<!doctype html><html><head><meta charset="utf-8"><title>DSM registry review</title><style>
body{{font:12px/1.45 system-ui,sans-serif;color:#222;margin:24px}}table{{border-collapse:collapse;width:100%;margin:6px 0 16px}}
td,th{{border:1px solid #999;padding:4px 6px;text-align:left;vertical-align:top}}td.comment{{width:28%}}.box{{white-space:nowrap}}
h2{{font-size:14px;margin-top:22px}}h2 small{{font-weight:normal;color:#555}}section{{break-inside:avoid-page}}</style></head><body>
<h1>DSM registry review ({esc(reg.tag)})</h1><p>{esc(PARAPHRASE_NOTE)} Printed {esc(date.today().isoformat())}. Tick a decision for every line and
write the suggested wording or rule for anything marked "changed". Return the CSV export with the <em>decision</em>, <em>verified_by</em>,
<em>verified_on</em> and <em>comment</em> columns filled in to record the decisions.</p>{''.join(blocks)}
<section><h2>Extraction features (what the model looks for in each session)</h2><table><tr><th>Key</th><th>Definition</th><th>Status</th><th>Decision</th><th>Comment</th></tr>{features}</table></section>
</body></html>"""


def import_csv(db: DbSession, reg: DsmRegistry, text: str, *, actor: str) -> dict:
    """Record decisions; ids and content are never modified. Returns {import_batch_id, recorded, rejected}."""
    reader = csv.DictReader(io.StringIO(text.lstrip("﻿")))
    missing = {"entity_kind", "id", "content_sha256", "decision"} - set(reader.fieldnames or [])
    if missing:
        raise ValueError(f"CSV is missing columns: {', '.join(sorted(missing))}")
    current = {(k, i): content_sha256(raw) for k, i, raw in reg.entries()}
    batch = f"regimp-{uuid.uuid4().hex[:12]}"
    recorded, rejected = 0, []
    for n, row in enumerate(reader, start=2):
        decision = (row.get("decision") or "").strip().lower()
        if not decision:
            continue
        key = ((row.get("entity_kind") or "").strip(), (row.get("id") or "").strip())
        reason = None
        if key not in current:
            reason = "unknown id"
        elif (row.get("content_sha256") or "").strip() != current[key]:
            reason = "content changed since export (hash mismatch); export again"
        elif decision not in DECISIONS:
            reason = "decision must be verified, changed or pending"
        elif decision == "changed" and not (row.get("comment") or "").strip():
            reason = "a 'changed' decision needs the suggested wording in the comment"
        verified_on = (row.get("verified_on") or "").strip() or date.today().isoformat()
        if reason is None:
            try:
                date.fromisoformat(verified_on)
            except ValueError:
                reason = "verified_on must be YYYY-MM-DD"
        if reason:
            rejected.append({"line": n, "entity_kind": key[0], "id": key[1], "reason": reason})
            continue
        db.add(RegistryVerificationRecord(id=f"regver-{uuid.uuid4().hex[:16]}", registry_kind="dsm", entity_kind=key[0], entity_id=key[1],
                                          content_sha256=current[key], status=decision, verified_by=(row.get("verified_by") or "").strip()[:120] or actor,
                                          verified_on=verified_on, comment=(row.get("comment") or "").strip()[:2000], import_batch_id=batch,
                                          created_at=utc_now()))
        recorded += 1
    db.flush()
    return {"import_batch_id": batch, "recorded": recorded, "rejected": rejected}


def main(argv=None) -> int:
    """python -m services.progress.dsm.verification export --csv out.csv [--html out.html] | import review.csv --actor NAME"""
    import argparse
    from pathlib import Path

    from services.api.main import session_factory

    from .registry import dsm_registry
    parser = argparse.ArgumentParser(description="Export the DSM registry for clinician review, or record the returned decisions.")
    sub = parser.add_subparsers(dest="command", required=True)
    exp = sub.add_parser("export")
    exp.add_argument("--csv", type=Path, required=True)
    exp.add_argument("--html", type=Path)
    imp = sub.add_parser("import")
    imp.add_argument("path", type=Path)
    imp.add_argument("--actor", required=True)
    args = parser.parse_args(argv)
    reg = dsm_registry()
    with session_factory() as db:
        if args.command == "export":
            args.csv.write_text(export_csv(db, reg), encoding="utf-8")
            if args.html:
                args.html.write_text(export_html(db, reg), encoding="utf-8")
            print(f"Exported {len(reg.entries())} entries ({reg.tag}).")
            return 0
        result = import_csv(db, reg, args.path.read_text(encoding="utf-8-sig"), actor=args.actor)
        db.commit()
    print(f"Recorded {result['recorded']} decisions (batch {result['import_batch_id']}).")
    for row in result["rejected"]:
        print(f"  line {row['line']} {row['entity_kind']} {row['id']}: {row['reason']}")
    return 0 if not result["rejected"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
