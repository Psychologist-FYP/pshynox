# Week 6 fuzzy retrieval

## Scope

The Week 6 module is an offline, lexical fuzzy-search demonstration over transcript segments. It supports user terms, quoted phrases, implicit `AND`, parentheses, `AND`, `OR`, and two `NOT` modes. It does not diagnose, infer a clinical state, score risk, or perform semantic search.

The API route is:

```text
GET /api/browse/fuzzy?q=&scope=&k=&not_mode=&window=
```

The result preserves the `EvidenceHit` fields and adds `memberships`, `parsed_query`, and a ±2 context window. The original `EvidenceHit` contract is unchanged.

## Membership definition

For a query term `t` and document `d`, the implementation computes:

```text
mu(t, d) = min_similarity(t, d) × tf_sat(d) × idf_norm(t)
```

with the result clamped to `[0, 1]`.

```text
tf_sat(d) = 1 - exp(-frequency)

idf(t) = ln(1 + (N - df(t) + 0.5) / (df(t) + 0.5))

idf_norm(t) = min(1, idf(t) / ln(1 + (N + 0.5) / 0.5))
```

Lexical similarity is deterministic:

| Match | Similarity |
|---|---:|
| Exact token | 1.0 |
| Prefix match | 0.8 |
| Same simple stem | 0.7 |
| Longer substring, with tokens at least three characters | 0.5 |
| No match | 0.0 |

Operators use set-theoretic fuzzy membership:

```text
mu(A AND B) = min(mu(A), mu(B))
mu(A OR B)  = max(mu(A), mu(B))
mu(NOT A)   = 1 - mu(A)
```

Strict-exclude mode treats `NOT A` as a set difference at the configured threshold `theta`:

```text
strict_not(A) = 0 if mu(A) >= theta, otherwise 1
```

Risk-related searches remain search candidates only. The API must not enable strict exclude/NOT behavior for risk-candidate scopes, and zero results never imply safety.

## Slide 16 — computed SOAP example

Fixture: `session-soap-demo-001`, five fictional transcript segments. Query: `sleep OR meals`. The table below is computed by the checked-in implementation over the five SOAP documents.

| Rank | Segment | Evidence excerpt | mu(sleep) | mu(meals) | OR score | Decision |
|---:|---|---|---:|---:|---:|---|
| 1 | `seg-soap-004` | “I sometimes skip meals…” | 0.000 | 0.353 | 0.353 | Include |
| 2 | `seg-soap-002` | “How has your sleep been…” | 0.223 | 0.000 | 0.223 | Include |
| 3 | `seg-soap-003` | “Sleep has been difficult…” | 0.223 | 0.000 | 0.223 | Include |

The remaining two segments have zero membership for both terms. Ties are stable: score descending, then timestamp, then segment ID.

### Include/exclude example

For `sleep AND NOT meals`, segment `seg-soap-003` has `mu(sleep)=0.223` and `mu(meals)=0`, so:

```text
AND score = min(0.223, 1 - 0) = 0.223
```

For a strict exclusion threshold of `theta=0.30`, a segment with `mu(meals)=0.353` is excluded because `0.353 >= 0.30`. This is a retrieval set operation, not a clinical conclusion.

## Two-minute demo script

1. Open `/browse` and point out that the default is `Structured`: Session > Section > Segment.
2. Switch to `Flat (baseline)` to show the same documents without hierarchy; switch to `Hypertext` and open a segment to show related links.
3. Select `Fuzzy comparison`, search `sleep OR meals`, and compare the BM25 baseline with fuzzy scores.
4. Expand a fuzzy result’s parsed query and show the per-term membership bars.
5. Click a context-window row. The selected hit is highlighted and the preview shows up to two neighbouring segments on each side.
6. Open `/soap-preview`. Show the five source segments, evidence chips, and traces for Subjective and Functioning.
7. Point out that Objective, Assessment, and Plan remain “Not documented”, and that the historical risk-language line is labelled “search candidates, not a risk assessment” with review status `PENDING`.

