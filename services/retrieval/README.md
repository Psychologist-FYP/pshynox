# Browse and retrieval navigation

This package provides the Week 5 browsing architecture over fictional transcript segments. It is offline and does not diagnose, assess risk, or assign clinical scores.

## Hierarchy

```text
FYP Repository
└── Scenario category
    └── Session
        └── Note section
            └── Transcript segment (document)
```

Category and section labels live in `packages/contracts/fixtures/browse_metadata.json`, so the shared transcript contract remains unchanged.

## Week 5 concepts

- Flat browsing: `/api/browse/documents` lists all segments in timestamp order.
- Structured guided browsing: `/api/browse/tree` and `/api/browse/node/{id}` expose one hierarchy level at a time.
- Hypertext: `/api/browse/related/{segment_id}` returns typed links for previous/next segments, same-section documents, and lexical similarity.

## Endpoints

| Endpoint | Purpose |
|---|---|
| `GET /api/browse/home` | Home summary and category counts |
| `GET /api/browse/tree?node_id=root` | Children for a hierarchy node |
| `GET /api/browse/node/{id}` | Breadcrumb, children, and documents |
| `GET /api/browse/documents` | Flat or scoped document list |
| `GET /api/browse/search?q=&scope=&k=` | Offline lexical search with optional node scope |
| `GET /api/browse/segment/{id}` | Full document preview |
| `GET /api/browse/related/{id}` | Typed related-document links |
| `GET /api/browse/graph` | Nodes and links for graph validation/visualization |

The current adapter uses a small deterministic BM25-style lexical scorer. It is intentionally isolated so the full Module B BM25/Boolean engine can replace the scorer without changing the browse API.
