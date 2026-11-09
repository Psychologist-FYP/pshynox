# Progress benchmark freeze rule
Declared 7 Nov 2026, before any development-arm results were viewed.
Arms: retrieval-current vs retrieval-prior (prompt progress-extract-v2, context 4096).
Full-transcript arms dropped: v2 exceeds the 4096 context, and full transcripts
cannot fit real 45-60 min sessions, so retrieval is the only deployable setting.
Rule: lowest change MAE. If 95% intervals overlap, lowest current-score MAE.
If those also overlap, choose retrieval-current (simpler, cheaper).
Sensitivity check: also report results excluding trajectory 1799_pdt_031
(used in the v1 timing pilot and diagnosis).
