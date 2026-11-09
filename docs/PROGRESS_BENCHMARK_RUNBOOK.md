# Progress benchmark runbook (LongCounsel-8, depression track)

Copy-paste commands, in order, for the user-run evaluation of the progress pipeline. **Synthetic benchmark
(LongCounsel-8): depression track only.** It measures Stage A PHQ-8 estimates and the Stage B depression hypothesis
against synthetic ground truth. It does not validate bipolar, GAD or social-anxiety hypotheses, and it is not clinical
validation. See [the plan](PROGRESS_MONITORING_PLAN.md) §8.

Every command runs from the repository root with the virtual environment. The CLIs read `.env` (GPU settings,
`NOTE_LLAMA_SERVER`, `NOTE_LIBRARY_PATH`), and exported variables take precedence. Prefix long runs with
`NOTE_LOG_LEVEL=events`, because `.env` sets `full`, which prints every prompt to the terminal.

```bash
cd /home/nachovarga/Astudy/FYP/psycho/Project/pshynox
source .venv/bin/activate
```

**While a run is in progress, do not transcribe audio, generate AI notes or analyse sessions in the app.** The
benchmark holds the shared model memory slot for the whole run, and the app's model jobs would wait and then fail.
Run arms **one at a time**.

## Estimated hours per step (progress-extract-v2)

Stage A uses **progress-extract-v2** (default; `PROGRESS_EXTRACT_PROMPT=v1` reproduces the earlier prompt). v2 passed
its pre-registered 6-session pilot on `val-rest-25`; see [PROGRESS_PROMPT_V2_PILOT.md](PROGRESS_PROMPT_V2_PILOT.md).
Measured per session (GTX 1650, 37/37 layers on GPU, 4,096 context, peak VRAM about 3.0 GiB): v2 retrieval Stage A
**205 s** (v2 pilot, 6 sessions; v1 was 175 s); Stage B depression **70–85 s** (v1 timing pilot; Stage B was not
re-timed with v2 inputs, so allow a margin).

| Step | Sessions | Estimated GPU hours |
|---|---:|---:|
| Arm `retrieval-current` (computes v2 retrieval Stage A) | 60 | **4.8** |
| Arm `retrieval-prior` (reuses that Stage A) | 60 | **1.4** |
| **Both retrieval arms** | | **≈ 6.2** |
| Optional calibration enlargement (`val-rest-25`, frozen arm, retrieval) | 125 | 10.0 |
| Final half-test run (`test-final-37`, frozen arm, retrieval) | 185 | **14.8** |

Allow about 20% extra: Stage B with prior history grows with the session number.

**Full-transcript arms are not runnable with v2 at NOTE_CONTEXT=4096.** The v2 prompt is longer than v1. v1
full-transcript prompts already reached 3,631 tokens, and calibrated on those counts a single-task v2 chunk needs about
4,300 tokens on every `val-arms-12` session. `full-current` and `full-prior` would extract nothing (the run log now
prints a `WARNING ... groups not extracted` line per affected session). The evidence comparison (retrieval vs full)
therefore needs one of:
1. **Skip it** and compare history settings only, with the two retrieval arms (recommended for now).
2. **Raise the context** (`NOTE_CONTEXT=6144`): more KV-cache memory on the 4 GB GPU, which is unmeasured; check one
   session first with a new run-id, and re-time.
3. **Run the full arms with v1** (`PROGRESS_EXTRACT_PROMPT=v1`). That compares two prompts as well as two evidence
   settings, so it is not a clean comparison.

## 0. Prepare the dataset (done once; seconds, no GPU)

The release is already under `data/research/datasets/longcounsel-8/raw/`. This verifies the SHA-256 hashes in
`manifest.json`, streams the archive (nothing is extracted into `raw/`) and writes research copies to `prepared/`:
transcripts with only the five visible fields, and labels in separate files.

```bash
python -m services.progress.evaluation.longcounsel prepare
```

Expected: 369 trajectories, splits `train 258 / val 37 / test 74`, `dropped_fields: {}`. The leakage guard also
counts dialogue rows that use words such as *scale* or *score* (36 rows, e.g. "on a scale of…"). These are part of
the visible dialogue, not hidden labels.

## 1. Timing pilot (2 validation sessions; already run by Claude)

```bash
NOTE_LOG_LEVEL=events python -m services.progress.evaluation.benchmark pilot
python -m services.progress.evaluation.benchmark estimate      # reprints the table above from the saved timings
```

## 2. Fixed subsets (DONE, committed in `1c04d55`)

`val-arms-12`, `test-final-37` and `val-rest-25` already exist. Do not regenerate them (the command refuses to
overwrite). For reference, they were created with:

```bash
python -m services.progress.evaluation.subsets select --seed 42 --val-n 12 --test-n 37
python -m services.progress.evaluation.subsets complement --of data/research/progress-subsets/val-arms-12.json
```

This prints each subset's derived trajectory-type counts and its number of large-change transitions
(|Δ ground-truth PHQ-8| ≥ 5). Stratification uses trajectory types **derived** from the ground-truth totals, because
the release has no type field. The command refuses to overwrite existing manifests. The other 37 test trajectories are
never listed and stay unused.

## 3. Run the development arms (validation, 12 trajectories × 5 sessions, v2)

Open one tmux session and run the two retrieval arms one after the other. `systemd-inhibit` keeps the laptop from
sleeping or idling while a run is active.

```bash
tmux new -s pshynox-bench
```

Inside tmux:

```bash
cd /home/nachovarga/Astudy/FYP/psycho/Project/pshynox && source .venv/bin/activate
export PROGRESS_EXTRACT_PROMPT=v2 NOTE_LOG_LEVEL=events
for ARM in retrieval-current retrieval-prior; do
  systemd-inhibit --what=idle:sleep --who=pshynox --why="LongCounsel-8 benchmark $ARM" \
    python -m services.progress.evaluation.benchmark run \
      --subset data/research/progress-subsets/val-arms-12.json --arm "$ARM" --run-id "val12-v2-$ARM" || break
done
```

`retrieval-prior` reuses the Stage A results of `retrieval-current`, because Stage A is cached per evidence setting and
prompt version. Each arm can also be run on its own. The run-ids are new, so earlier v1 timing runs cannot mix in: the
run config records `extract_prompt` and refuses any mismatch.

**Status (8 November 2026):** both retrieval arms are complete (60/60 sessions each). **The user decided not to run the
full-transcript arms** (`full-current`, `full-prior`): with v2 they are estimated ineligible at 4,096 context, and the
freeze rule ([PROGRESS_FREEZE_RULE.md](PROGRESS_FREEZE_RULE.md)) already chooses between the retrieval arms. Steps 6–8
(reports, freeze, calibration, final run) remain available and optional. The commands below are kept for reference.

Full-transcript arms (not run; only after choosing an option in "Estimated hours per step"):

```bash
# Option 2 (larger context), checked on one arm first:
NOTE_CONTEXT=6144 python -m services.progress.evaluation.benchmark run \
  --subset data/research/progress-subsets/val-arms-12.json --arm full-current --run-id val12-v2-ctx6144-full-current
```

Detach with **Ctrl+b then d**. Reattach with `tmux attach -t pshynox-bench`.

## 4. Check progress

```bash
tail -f data/research/progress-runs/val12-v2-retrieval-current/run.log        # one line per session with ETA
python -m services.progress.evaluation.benchmark status --run-id val12-v2-retrieval-current
nvidia-smi                                                                  # llama-server should hold GPU memory
```

`run.log` contains ids, timings, labels and PHQ-8 totals only, never transcript text.

## 5. Stop, crash, reboot: resume

- **Ctrl+C once** stops safely. The in-flight session is abandoned and no partial checkpoint or Stage A cache entry is
  written, llama-server is closed, and the exit code is 130. **Ctrl+C twice** forces an exit.
- **To resume, rerun exactly the same command** (same `--subset`, `--arm`, `--run-id`). Completed sessions are skipped
  automatically, and the model-call checkpoint cache also resumes inside an interrupted session.
- A run-id is bound to its configuration (code hashes of the pipeline, model SHA-256, NOTE_* runtime settings, subset
  hash). If anything changed, the command refuses and lists the differences; use a new `--run-id`.
- If the command says the run is already running, another process holds it (`ps aux | grep benchmark`).

## 6. Build the reports (works on partial runs, which are marked incomplete)

```bash
python -m services.progress.evaluation.report --run-id val12-v2-retrieval-current --run-id val12-v2-retrieval-prior
```

For each run this writes `data/research/progress-runs/<run-id>/report/report.json`, `report.md` and
`citation_rating.csv`, and it prints a comparison table. The reports contain current-score MAE, item MAE and exact match,
change MAE, large-change direction accuracy (ties counted wrong), MAE by trajectory type, coverage, AUROC/AUPRC,
sensitivity/specificity/PPV/NPV/F1 at `possible` and `likely`, cross-fitted Brier score and reliability, trajectory
agreement, the train-self-report baselines and runtime. Intervals come from a 2,000-resample trajectory bootstrap with
seed 42.

Open **<http://127.0.0.1:5173/workspace/research-progress>** (start the app with `./scripts/run.py` *after* the runs)
for tables, ROC and reliability curves, per-trajectory plots and per-session cited findings.

**Citation quality:** give `citation_rating.csv` (100 random cited findings per run) to the team or the clinical
consultant. Fill the `supports_claim (yes/partly/no)` and `rater` columns by hand. They are left empty on purpose.

## 7. Choose and freeze the configuration (once)

Decide the rule **before** looking at the numbers and write it in `--reason`. Suggested rule with the two retrieval
arms: lowest change MAE; if the 95% intervals overlap, lowest current-score MAE; if those also overlap, prefer
`retrieval-current` (cheaper Stage B). Report item MAE and coverage alongside totals (see the v2 pilot caveats).

```bash
python -m services.progress.evaluation.benchmark freeze --run-id val12-v2-<chosen-arm> \
  --reason "Pre-declared rule: lowest change MAE; ties -> current MAE; then retrieval"
git add data/research/progress-frozen/frozen-progress-config.json
git commit -m "Freeze progress benchmark configuration"
```

Freezing records the code hashes, model and runtime settings, and fits the isotonic calibration (AI PHQ-8 total →
P(PHQ-8 ≥ 10)) on the chosen validation run. It refuses to run twice. **Do not change pipeline code after freezing**;
`run_final` checks this.

### 7b. Optional: enlarge the calibration set with the other 25 validation trajectories

```bash
python -m services.progress.evaluation.subsets complement --of data/research/progress-subsets/val-arms-12.json
PROGRESS_EXTRACT_PROMPT=v2 NOTE_LOG_LEVEL=events systemd-inhibit --what=idle:sleep --who=pshynox --why="calibration run" \
  python -m services.progress.evaluation.benchmark run --subset data/research/progress-subsets/val-rest-25.json --arm frozen --run-id val25-v2-frozen
python -m services.progress.evaluation.benchmark calibrate --run-id val12-v2-<chosen-arm> --run-id val25-v2-frozen
python -m services.progress.evaluation.report --run-id val25-v2-frozen
```

## 8. Final half-test run (once, user only)

```bash
tmux new -s pshynox-final
cd /home/nachovarga/Astudy/FYP/psycho/Project/pshynox && source .venv/bin/activate
PROGRESS_EXTRACT_PROMPT=v2 NOTE_LOG_LEVEL=events systemd-inhibit --what=idle:sleep --who=pshynox --why="LongCounsel-8 final run" \
  python -m services.progress.evaluation.run_final --subset data/research/progress-subsets/test-final-37.json
```

`run_final` checks that the frozen configuration and code are unchanged, records `evaluation-started.json`, runs the
frozen arm on the 37 test trajectories (resume by rerunning the same command), then writes
`data/research/progress-runs/final-test/report/final-results.json`. The frozen calibration is applied without being
refit. It refuses to overwrite final results. **Do not iterate or tune after seeing them.**

## 9. Optional: demo patients for the clinical UI

Synthetic, clearly labelled patients for demonstrating the dashboard. Validation trajectories only (the test split is
blocked); transcripts only, no labels; the SQLite database is backed up first.

```bash
# 12 patients with the AI outputs a finished validation run already produced (no GPU, seconds):
python -m services.api.import_longcounsel_demo --with-results val12-v2-retrieval-current
# transcript-only patients for running the analysis live from the UI:
python -m services.api.import_longcounsel_demo --split validation --limit 16
```

`--with-results` attaches Stage A (domains, PHQ-8, GAD-7, risk statements) from the run's Stage A cache and the Stage B
depression hypothesis from its checkpoints, as AI-generated values pending review. Checkpoints do not keep rule-outs,
differentials or missing-information questions; those hypotheses say so in their notes. Regenerate from the UI for full
output and for bipolar, GAD and social anxiety. Re-running never overwrites existing values.

Analyse their sessions from each session's **Observations** panel. Calibrated probabilities appear only for these
`lc8-demo-` patients, and only after a calibration exists (dataset terms; plan Q3).

## Troubleshooting

| Symptom | Fix |
|---|---|
| `memory slot busy` / `MODEL_BUSY` | Another model job is running (app transcription, note drafting, another arm). Wait, then rerun. |
| `GPU offload was requested but no layers were confirmed` | See [NVIDIA_LOCAL_FIX.md](NVIDIA_LOCAL_FIX.md); for a CPU run set `NOTE_GPU_LAYERS=0`. This is a different runtime configuration, so use a new run-id. |
| `Refusing to mix configurations` | Code, model, `.env` NOTE_* settings or the subset changed. Restore them, or use a new `--run-id`. |
| Session `stageB ... label=None` | Stage B failed for that session (recorded in its checkpoint); it counts as missing in the threshold metrics. |
| Disk | Runs are small (JSON checkpoints); the shared model-call cache lives in `data/research/progress-runs/_work/cache-v4/`. |
