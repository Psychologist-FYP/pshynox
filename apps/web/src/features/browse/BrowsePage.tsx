import { useEffect, useState, type FormEvent } from "react";
import { Icon } from "../../components/ui/Icon";
import { WorkspaceShell } from "../../components/layout/AppShell";
import { api, formatTimestamp, type BrowseDocument, type BrowseHome, type BrowseLink, type BrowseNode, type BrowseNodeResponse, type Health } from "../../lib/api";
import "./browse.css";

export function BrowsePage({ health }: { health: Health | null }) {
  const [home, setHome] = useState<BrowseHome | null>(null);
  const [node, setNode] = useState<BrowseNodeResponse | null>(null);
  const [selected, setSelected] = useState<BrowseDocument | null>(null);
  const [related, setRelated] = useState<BrowseLink[]>([]);
  const [query, setQuery] = useState("");
  const [searching, setSearching] = useState(false);
  const [searchResults, setSearchResults] = useState<BrowseDocument[] | null>(null);
  const [scopeSearch, setScopeSearch] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.browseHome().then((value) => { setHome(value); return api.browseNode("root"); }).then(setNode).catch((reason: Error) => setError(reason.message));
  }, []);

  async function openNode(nodeId: string) {
    setError(null); setSearchResults(null);
    try { setNode(await api.browseNode(nodeId)); setSelected(null); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "The browse node could not be opened."); }
  }

  async function openDocument(document: BrowseDocument) {
    setSelected(document); setError(null);
    try { const preview = await api.browsePreview(document.segment_id); setRelated(preview.related); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "The document preview could not be opened."); }
  }

  async function submitSearch(event: FormEvent) {
    event.preventDefault();
    if (!query.trim()) { setSearchResults(null); return; }
    setSearching(true); setError(null);
    try { setSearchResults((await api.browseSearch(query, scopeSearch ? node?.node_id : undefined)).results); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "Search could not be completed."); }
    finally { setSearching(false); }
  }

  const documents = searchResults ?? node?.documents ?? [];
  return <WorkspaceShell health={health} active="Browse">
    <main className="browse-page section-wrap">
      <section className="browse-intro"><div><p className="eyebrow">Week 5 · browsing architecture</p><h2>{home?.title ?? "FYP Repository"}</h2><p>{home?.hint ?? "Explore fictional transcript material step by step."}</p></div><div className="browse-modes" aria-label="Browsing models"><span className="mode-chip active">Flat</span><span className="mode-chip active">Structured guided</span><span className="mode-chip active">Hypertext</span></div></section>
      <SearchBar query={query} scopeSearch={scopeSearch} hasNode={Boolean(node && node.node_id !== "root")} searching={searching} onQuery={setQuery} onScope={setScopeSearch} onSubmit={submitSearch} />
      {error && <div className="state-panel state-error"><strong>Browse unavailable</strong><span>{error}</span></div>}
      <div className="browse-layout">
        <aside className="browse-sidebar panel"><p className="eyebrow">Structured guided browsing</p><h3>Scenario categories</h3>{home?.categories.map((category) => <button className={`browse-category ${node?.node_id === `category:${category.id}` ? "active" : ""}`} key={category.id} onClick={() => openNode(`category:${category.id}`)}><span>{category.label}</span><small>{category.count} excerpts</small></button>)}</aside>
        <section className="browse-results panel"><Breadcrumb items={node?.breadcrumb ?? []} onOpen={openNode} /><div className="panel-heading"><div><p className="eyebrow">{searchResults ? "Search results" : "Document list"}</p><h3>{searchResults ? `Matches for “${query}”` : node?.title ?? "Loading browse index"}</h3></div><span className="small-note">{documents.length} documents</span></div>{searchResults && !documents.length && <div className="empty-inline">No results found. An empty result is not a safety conclusion.</div>}{!searchResults && node?.children.length ? <div className="browse-children">{node.children.map((child) => <button key={child.id} onClick={() => openNode(child.id)}><span>{child.label}</span><small>{child.count ?? 0}</small><Icon name="arrow" size={14} /></button>)}</div> : null}<DocumentList documents={documents} selectedId={selected?.segment_id} onOpen={openDocument} /></section>
        {selected && <DocumentPreview document={selected} related={related} onOpen={openDocument} />}
      </div>
    </main>
  </WorkspaceShell>;
}

function SearchBar({ query, scopeSearch, hasNode, searching, onQuery, onScope, onSubmit }: { query: string; scopeSearch: boolean; hasNode: boolean; searching: boolean; onQuery: (value: string) => void; onScope: (value: boolean) => void; onSubmit: (event: FormEvent) => void }) {
  return <form className="browse-search" onSubmit={onSubmit}><label htmlFor="browse-query">Search or browse the fictional repository</label><div className="browse-search-row"><Icon name="search" size={16} /><input id="browse-query" value={query} onChange={(event) => onQuery(event.target.value)} placeholder="Try sleep difficulty" /><button className="button button-primary" disabled={searching}>{searching ? "Searching…" : "Search"}</button></div><label className="scope-toggle"><input type="checkbox" checked={scopeSearch} disabled={!hasNode} onChange={(event) => onScope(event.target.checked)} /> Search within this node {hasNode ? "" : "(select a category or deeper node first)"}</label></form>;
}

function Breadcrumb({ items, onOpen }: { items: BrowseNode[]; onOpen: (id: string) => void }) {
  return <nav className="browse-breadcrumb" aria-label="Browse breadcrumb">{items.map((item, index) => <span key={item.id}>{index > 0 && <b>›</b>}<button onClick={() => onOpen(item.id)}>{item.label}</button></span>)}</nav>;
}

function DocumentList({ documents, selectedId, onOpen }: { documents: BrowseDocument[]; selectedId?: string; onOpen: (document: BrowseDocument) => void }) {
  return <div className="browse-document-list">{documents.map((document) => <button className={`browse-document ${selectedId === document.segment_id ? "selected" : ""}`} key={document.segment_id} onClick={() => onOpen(document)}><span className="browse-document-meta"><strong>{document.speaker_role}</strong><span>{document.session_id}</span><span>{formatTimestamp(document.start_ms)}–{formatTimestamp(document.end_ms)}</span></span><strong>{document.title}</strong><span>{document.excerpt}</span></button>)}</div>;
}

function DocumentPreview({ document, related, onOpen }: { document: BrowseDocument; related: BrowseLink[]; onOpen: (document: BrowseDocument) => void }) {
  return <aside className="browse-preview panel"><p className="eyebrow">Document preview</p><h3>{document.title}</h3><p className="preview-copy">{document.text}</p><dl className="preview-facts"><div><dt>Speaker</dt><dd>{document.speaker_role}</dd></div><div><dt>Time</dt><dd>{formatTimestamp(document.start_ms)}–{formatTimestamp(document.end_ms)}</dd></div><div><dt>Session</dt><dd>{document.session_id}</dd></div><div><dt>Section</dt><dd>{document.section_label}</dd></div></dl><p className="eyebrow related-heading">Related documents</p>{related.length ? related.map((link) => <button className="related-link" key={`${link.link_type}-${link.target_id}`} onClick={() => onOpen(link.document)}><span>{link.link_type.replaceAll("_", " ")}</span><strong>{link.label}</strong></button>) : <p className="empty-inline">No related documents in this fixture.</p>}</aside>;
}
