# Team retrieval annotation

Use the shared `annotation` branch. It contains the prepared AnnoMI and PriMock
transcripts and existing annotation progress, so a fresh checkout can open the
research page without running the starter-pack preparation command. Follow the
root README for application dependencies and setup.

## Join the branch

From an existing clone with your other work committed:

```bash
git fetch origin
git switch annotation
git pull --ff-only origin annotation
./scripts/run.py
```

If you have no local `annotation` branch, `git switch annotation` creates one
tracking `origin/annotation`. For a new clone:

```bash
git clone --branch annotation git@github.com:Psychologist-FYP/pshynox.git
cd pshynox
```

Open <http://127.0.0.1:5173/workspace/research>. Agree with the team on which
**conversation IDs** each member owns before annotating. Give one member all
queries in a conversation, use your own reviewer name, and keep the existing
development/test assignments. See [the annotation controls](RESEARCH_EVALUATION.md).

## Save and share your work

The app automatically saves directly into these tracked files:

```text
data/research/datasets/annomi.json
data/research/datasets/primock.json
data/research/annotations/annomi/<conversation-id>.json
data/research/annotations/primock/<conversation-id>.json
```

Each conversation file includes its queries, grades (including explicit zero),
reviewer/rationale/timestamps, no-answer confirmations, pilot expansion, and
exploration exposure. Partial progress is shareable; evaluation still requires
complete, approved queries. Do not edit prepared transcripts or split metadata
while collecting annotations.

After annotating, **stop the app** before committing or pulling. From the repository
root (replace the example ID with your assigned conversation; add more paths if
you worked on several):

```bash
git status --short
git diff -- data/research/annotations/annomi/annomi-24.json
git add data/research/annotations/annomi/annomi-24.json
git commit -m "Annotate annomi-24 relevance queries"
git pull --rebase origin annotation
git push origin annotation
```

If a teammate pushed between your pull and push, repeat the last two commands.
Commit your own work before pulling; avoid stashing or discarding unsaved annotation
changes. Separate conversation files let Git combine work on different conversations.
If a rebase stops on a conflict, coordinate with the other author, preserve both
sets of intended changes, and review any combined query wording and grades together.
Then stage the resolved file and run `git rebase --continue`. Never resolve an
annotation conflict by blindly choosing an entire side. `git rebase --abort`
returns to your pre-rebase work if you need help resolving it.

Restart the app or reload the research page after pulling. Already open tabs may
ask you to reload before saving because another member's changes altered progress.
Fetching alone downloads remote commits; **pulling** applies them to this checkout.

## Collect annotations and evaluate

Use the same commit/pull/push workflow for your own subsequent annotation work.
When collecting everyone else's work, stop the app, commit any local changes,
then run:

```bash
git pull --rebase origin annotation
./scripts/run.py
```

The research page and evaluation commands read the combined conversation files
directly; no import or manual dataset concatenation is needed. In the UI, select
the dataset and split, then export the validated dataset or run evaluation. Export
is blocked until every query in that pilot split is approved and fully reviewed,
with explicit confirmation for all-zero/no-answer queries. See
[evaluation instructions](RESEARCH_EVALUATION.md) for CLI benchmarks and freezing
development settings before held-out testing.

## Existing local progress and excluded files

The original local `annotations/annomi.json` and `annotations/primock.json` remain
ignored as backups of progress before conversion. The app uses conversation files
when their dataset directory exists; the old monolithic files are no longer live.
The initial conversion preserves all saved query and judgment fields.

To convert an older, separate research workspace **before** combining it with this
branch, stop its app and run:

```bash
.venv/bin/python -m services.retrieval.share_annotations --root /path/to/research
```

Keep a backup and merge its conversation files with the team's version, reviewing
overlaps. This command is idempotent for an already converted workspace; it does
not import later changes made to the old ignored monolithic backups.

Clinical databases, personal recordings, `.env`, keys, model weights, local tools,
benchmark output, and generated audio remain ignored. Original starter CSVs/audio
are also local; `services.retrieval.prepare` verifies that full source pack and is
only needed to rebuild imports when you have those original files. The prepared
transcripts on this branch are enough for annotation and retrieval evaluation.
Publisher README and PriMock license files are included under
`docs/Pshynox_Retrieval_Starter/data/`; retain their attribution and usage terms.
