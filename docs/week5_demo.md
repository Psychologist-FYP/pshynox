# Week 5 browse demo

Use fictional fixture data only. Start FastAPI from the repository root and open `/browse` in the Vite app.

## Two-to-three minute script

1. **Home page:** Open `/browse`. Point out the category cards, segment/session counts, search box, and the short browsing hint.
2. **Structured guided browsing:** Select `Everyday functioning`, then the session, then `Subjective`. The breadcrumb shows `FYP Repository > Everyday functioning > Session > Subjective`; click an earlier crumb to move back up.
3. **Document list and preview:** Select the sleep-related transcript segment. The list remains visible while the side panel shows its full text, speaker, timestamps, session, category, and section.
4. **Search + browse:** Search for `sleep difficulty` globally, then repeat with `This node` selected. Both views use the same document result shape. Search `not present` to demonstrate a safe `No results found` state.
5. **Hypertext links:** Use the related links to jump to a previous/next segment, another session/section when available, or a lexically similar excerpt. Explain that these are typed links `L = (source_id, target_id, link_type)`.

## Behaviour examples

- Exploratory: start at Home and open categories without knowing which segment is needed.
- Directed: search for `sleep difficulty`, then open the matching source segment.
- Opportunistic: while previewing a segment, follow a related-document link to a non-linear neighbour.

The risk-related category is labelled as search candidates only. It is not a risk assessment, diagnosis, or safety conclusion.
