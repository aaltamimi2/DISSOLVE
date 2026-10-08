// The literature corpus as papers to browse: a list of its papers with their records, searched in the browser, and a
// search inside their text that groups the passages the literature search ranks by paper (/api/literature/*).
import { ArrowLeft, BookOpen, ExternalLink, FileSearch, Loader2, MessageSquareText, Search, X } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { api, type Paper, type PaperHit, type PaperList, type PaperSearch } from "./api";
import { shortCitation } from "./citations";

export { shortCitation };

type Sort = "relevance" | "newest" | "oldest" | "cited" | "title";
const SORT_LABEL: Record<Sort, string> = {
  relevance: "Best match",
  newest: "Newest",
  oldest: "Oldest",
  cited: "Most cited",
  title: "Title",
};

const STOP = new Set(
  "a an and are as at be by for from has have in into is it its of on or that the their this to was were with which what how do does".split(" "),
);

/** Lower-case word stems without accents, stop words or one-letter tokens. */
export function tokens(text: string): string[] {
  const words = text.toLowerCase().normalize("NFKD").replace(/[\u0300-\u036f]/g, "").match(/[a-z0-9]+/g) ?? [];
  return words
    .filter((w) => (w.length > 1 || /\d/.test(w)) && !STOP.has(w))
    .map((w) => (w.length > 3 && w.endsWith("s") && !w.endsWith("ss") ? w.slice(0, -1) : w));
}

const FIELDS: [keyof Paper | "authorsText", number][] = [
  ["title", 3],
  ["authorsText", 2],
  ["year", 2],
  ["venue", 1],
  ["abstract", 1],
  ["doi", 1],
];

/** Every paper matching all query words (the last word also as a prefix, for typing), ranked by a BM25-like score; if
 * none matches all of them, the papers matching any, flagged as partial. */
export function matchPapers(papers: Paper[], query: string): { scores: Map<string, number>; partial: boolean } {
  const wanted = tokens(query);
  const scores = new Map<string, number>();
  if (!wanted.length) return { scores, partial: false };
  const docs = papers.map((p) => {
    const view: Record<string, string> = { ...(p as unknown as Record<string, string>), authorsText: (p.authors ?? []).join(" ") };
    return { id: p.sha256, fields: FIELDS.map(([name, weight]) => [tokens(String(view[name] ?? "")), weight] as const) };
  });
  const hits = (term: string, last: boolean, words: string[]) =>
    words.filter((w) => w === term || (last && term.length >= 2 && w.startsWith(term))).length;
  const df = wanted.map((term, i) => docs.filter((d) => d.fields.some(([words]) => hits(term, i === wanted.length - 1, words) > 0)).length);
  const scoreAll = (requireAll: boolean) => {
    for (const d of docs) {
      let total = 0;
      let matched = 0;
      wanted.forEach((term, i) => {
        const last = i === wanted.length - 1;
        let tf = 0;
        for (const [words, weight] of d.fields) tf += weight * hits(term, last, words);
        if (tf > 0) {
          matched += 1;
          const idf = Math.log(1 + (docs.length - df[i] + 0.5) / (df[i] + 0.5));
          total += (idf * tf * 2.2) / (tf + 1.2);
        }
      });
      if (matched && (!requireAll || matched === wanted.length)) scores.set(d.id, total + matched);
    }
  };
  scoreAll(true);
  if (scores.size) return { scores, partial: false };
  scoreAll(false);
  return { scores, partial: scores.size > 0 };
}

function byline(paper: Paper): string {
  const authors = paper.authors ?? [];
  const names = authors.length > 3 ? `${authors.slice(0, 3).join(", ")} et al.` : authors.join(", ");
  return [names, paper.year, paper.venue].filter(Boolean).join(" · ");
}

let corpusPapers: Promise<Paper[]> | null = null;

/** The corpus papers, fetched once for every answer that cites them; null until they arrive. */
export function useCorpusPapers(needed: boolean): Paper[] | null {
  const [papers, setPapers] = useState<Paper[] | null>(null);
  useEffect(() => {
    if (!needed) return;
    let live = true;
    corpusPapers ??= api
      .papers()
      .then((list) => list.papers)
      .catch(() => {
        corpusPapers = null; // a failed fetch is tried again by the next answer
        return [];
      });
    void corpusPapers.then((list) => {
      if (live) setPapers(list);
    });
    return () => {
      live = false;
    };
  }, [needed]);
  return papers;
}

/** A paper to open on: the hash prefix a citation names, with a nonce so the same citation opens it again. */
export type PaperFocus = { hex: string; nonce: number };

export function PapersPanel({ onClose, onAsk, focus }: { onClose: () => void; onAsk: (paper: Paper) => void; focus?: PaperFocus | null }) {
  const [list, setList] = useState<PaperList | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [sort, setSort] = useState<Sort>("newest");
  const [from, setFrom] = useState<number | "">("");
  const [to, setTo] = useState<number | "">("");
  const [open, setOpen] = useState<{ paper: Paper; hit?: PaperHit } | null>(null);
  const [inside, setInside] = useState<PaperSearch | null>(null);
  const [searching, setSearching] = useState(false);

  useEffect(() => {
    api.papers().then(setList).catch((e: Error) => setError(e.message));
  }, []);

  useEffect(() => {
    const paper = focus ? list?.papers.find((p) => p.sha256.startsWith(focus.hex)) : undefined;
    if (paper) setOpen({ paper });
  }, [focus, list]);

  const years = useMemo(() => {
    const all = (list?.papers ?? []).map((p) => p.year).filter((y): y is number => typeof y === "number");
    return all.length ? Array.from(new Set(all)).sort((a, b) => a - b) : [];
  }, [list]);

  const { shown, partial } = useMemo(() => {
    const papers = (list?.papers ?? []).filter(
      (p) => (from === "" || (p.year ?? 0) >= from) && (to === "" || (p.year ?? 9999) <= to),
    );
    const { scores, partial } = matchPapers(papers, query);
    const kept = query.trim() ? papers.filter((p) => scores.has(p.sha256)) : papers;
    const order: Record<Sort, (a: Paper, b: Paper) => number> = {
      relevance: (a, b) => (scores.get(b.sha256) ?? 0) - (scores.get(a.sha256) ?? 0),
      newest: (a, b) => (b.year ?? 0) - (a.year ?? 0),
      oldest: (a, b) => (a.year ?? 9999) - (b.year ?? 9999),
      cited: (a, b) => (b.cited_by_count ?? -1) - (a.cited_by_count ?? -1),
      title: (a, b) => (a.title ?? "~").localeCompare(b.title ?? "~"),
    };
    const key: Sort = query.trim() || sort !== "relevance" ? sort : "newest";
    return { shown: [...kept].sort((a, b) => order[key](a, b) || (a.title ?? "").localeCompare(b.title ?? "")), partial };
  }, [list, query, sort, from, to]);

  const changeQuery = (value: string) => {
    if (!query.trim() && value.trim() && sort === "newest") setSort("relevance");
    if (!value.trim() && sort === "relevance") setSort("newest");
    setQuery(value);
    setInside(null);
  };

  const searchInside = async () => {
    const q = query.trim();
    if (!q || searching) return;
    setSearching(true);
    setOpen(null);
    try {
      setInside(await api.searchPapers(q));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setSearching(false);
    }
  };

  const count = list?.count ?? 0;
  return (
    <>
      <div className="fixed inset-0 z-30 bg-black/40 lg:hidden" aria-hidden onClick={onClose} />
      <aside
        aria-label="Literature corpus papers"
        className="rise fixed inset-x-0 bottom-0 z-40 flex h-[85dvh] flex-col rounded-t-2xl border-t border-line bg-surface shadow-float lg:static lg:z-auto lg:h-auto lg:w-[480px] lg:shrink-0 lg:rounded-none lg:border-l lg:border-t-0 lg:shadow-none"
      >
        <header className="flex items-center gap-2.5 border-b border-line px-4 py-3">
          <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-brand-tint text-brand-ink">
            <BookOpen size={16} />
          </span>
          <div className="min-w-0 flex-1">
            <h2 className="truncate font-headline text-[15px] font-semibold text-ink">Literature corpus</h2>
            <p className="font-headline text-xs text-ink-2">
              {list ? `${count} papers · the passages the literature search reads` : "Loading the papers"}
            </p>
          </div>
          <button type="button" onClick={onClose} aria-label="Close the papers" title="Close the papers" className="rounded-lg p-2 text-ink-2 hover:bg-muted hover:text-ink">
            <X size={17} />
          </button>
        </header>

        {open ? (
          <PaperDetail paper={open.paper} hit={open.hit} onBack={() => setOpen(null)} onAsk={onAsk} />
        ) : (
          <>
            <div className="space-y-2 border-b border-line px-4 py-3">
              <div className="flex gap-2">
                <label className="relative min-w-0 flex-1">
                  <span className="sr-only">Search the papers</span>
                  <Search size={15} className="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 text-ink-2" />
                  <input
                    value={query}
                    onChange={(e) => changeQuery(e.target.value)}
                    onKeyDown={(e) => {
                      if (e.key === "Enter") void searchInside();
                    }}
                    placeholder="Title, author, year, venue or topic"
                    className="h-9 w-full rounded-lg border border-line bg-canvas pl-8 pr-2 text-sm text-ink outline-none focus:border-brand-soft"
                  />
                </label>
                <label>
                  <span className="sr-only">Sort the papers</span>
                  <select
                    value={sort}
                    onChange={(e) => setSort(e.target.value as Sort)}
                    className="h-9 cursor-pointer rounded-lg border border-line bg-muted px-2 text-sm text-ink"
                  >
                    {(Object.keys(SORT_LABEL) as Sort[])
                      .filter((key) => key !== "relevance" || query.trim())
                      .map((key) => (
                        <option key={key} value={key}>
                          {SORT_LABEL[key]}
                        </option>
                      ))}
                  </select>
                </label>
              </div>
              <div className="flex flex-wrap items-center gap-2 font-headline text-xs text-ink-2">
                <span>Years</span>
                <YearSelect label="From year" years={years} value={from} onChange={setFrom} />
                <span aria-hidden>to</span>
                <YearSelect label="To year" years={years} value={to} onChange={setTo} />
                <button
                  type="button"
                  onClick={() => void searchInside()}
                  disabled={!query.trim() || searching}
                  title="Rank the passages of every paper, as the agent's literature search does"
                  className="ml-auto flex h-8 items-center gap-1.5 rounded-lg bg-muted px-2.5 text-xs font-medium text-ink hover:bg-line disabled:cursor-not-allowed disabled:opacity-50"
                >
                  {searching ? <Loader2 size={14} className="animate-spin" /> : <FileSearch size={14} />}
                  Search inside the papers
                </button>
              </div>
            </div>

            <div className="min-h-0 flex-1 overflow-y-auto px-4 py-3">
              {error && <p className="rounded-lg bg-bad/10 p-3 text-sm text-bad">{error}</p>}
              {!list && !error && <p className="text-sm text-ink-2">Loading…</p>}
              {list && inside && (
                <InsideResults
                  search={inside}
                  onOpen={(hit) => setOpen({ paper: hit.paper, hit })}
                  onBack={() => setInside(null)}
                />
              )}
              {list && !inside && (
                <>
                  <p className="mb-2 font-headline text-xs text-ink-2">
                    {query.trim() || from !== "" || to !== "" ? `${shown.length} of ${count} papers` : `${count} papers`}
                    {partial && " · no paper has every word, so these have some of them"}
                    {query.trim() && " · press Enter to search inside the papers"}
                  </p>
                  <ul className="space-y-2">
                    {shown.map((paper) => (
                      <li key={paper.sha256}>
                        <PaperRow paper={paper} onOpen={() => setOpen({ paper })} />
                      </li>
                    ))}
                  </ul>
                </>
              )}
            </div>
          </>
        )}
      </aside>
    </>
  );
}

function YearSelect({ label, years, value, onChange }: { label: string; years: number[]; value: number | ""; onChange: (v: number | "") => void }) {
  return (
    <label>
      <span className="sr-only">{label}</span>
      <select
        value={value}
        onChange={(e) => onChange(e.target.value === "" ? "" : Number(e.target.value))}
        className="h-8 cursor-pointer rounded-lg border border-line bg-muted px-1.5 text-xs text-ink"
      >
        <option value="">any</option>
        {years.map((y) => (
          <option key={y} value={y}>
            {y}
          </option>
        ))}
      </select>
    </label>
  );
}

function PaperRow({ paper, onOpen }: { paper: Paper; onOpen: () => void }) {
  return (
    <button
      type="button"
      onClick={onOpen}
      className="w-full rounded-xl border border-line bg-canvas p-3 text-left transition-colors hover:border-brand-soft hover:bg-muted"
    >
      <p className="line-clamp-2 text-[14px] font-semibold leading-snug text-ink">
        {paper.resolved ? paper.title : "Unidentified paper"}
      </p>
      <p className="mt-1 line-clamp-1 font-headline text-xs text-ink-2">
        {paper.resolved ? byline(paper) : paper.reason}
      </p>
      <p className="mt-1 font-headline text-[11px] text-ink-2">
        {paper.passages} passages{paper.pages ? ` · ${paper.pages} pages` : ""}
        {typeof paper.cited_by_count === "number" ? ` · cited ${paper.cited_by_count} times` : ""}
      </p>
    </button>
  );
}

function InsideResults({ search, onOpen, onBack }: { search: PaperSearch; onOpen: (hit: PaperHit) => void; onBack: () => void }) {
  return (
    <div>
      <div className="mb-2 flex items-center gap-2">
        <button type="button" onClick={onBack} className="flex items-center gap-1 rounded-lg px-1.5 py-1 font-headline text-xs text-ink-2 hover:bg-muted hover:text-ink">
          <ArrowLeft size={14} /> All papers
        </button>
        <p className="font-headline text-xs text-ink-2">
          {search.results.length
            ? `${search.results.length} papers with passages matching “${search.query}”`
            : `No passage matches “${search.query}”`}
        </p>
      </div>
      <ul className="space-y-2">
        {search.results.map((hit) => (
          <li key={hit.paper.sha256}>
            <button
              type="button"
              onClick={() => onOpen(hit)}
              className="w-full rounded-xl border border-line bg-canvas p-3 text-left transition-colors hover:border-brand-soft hover:bg-muted"
            >
              <p className="line-clamp-2 text-[14px] font-semibold leading-snug text-ink">
                {hit.paper.resolved ? hit.paper.title : "Unidentified paper"}
              </p>
              <p className="mt-1 line-clamp-1 font-headline text-xs text-ink-2">{byline(hit.paper)}</p>
              <p className="mt-2 line-clamp-3 border-l-2 border-brand-soft pl-2 text-xs leading-relaxed text-ink-2">
                {hit.passages[0]?.page ? <span className="font-medium text-ink">p. {hit.passages[0].page} · </span> : null}
                {hit.passages[0]?.excerpt}
              </p>
              {hit.passages.length > 1 && (
                <p className="mt-1 font-headline text-[11px] text-ink-2">+{hit.passages.length - 1} more matching passages</p>
              )}
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}

function PaperDetail({ paper, hit, onBack, onAsk }: { paper: Paper; hit?: PaperHit; onBack: () => void; onAsk: (paper: Paper) => void }) {
  const links = [
    paper.doi && { href: `https://doi.org/${paper.doi}`, label: `DOI ${paper.doi}` },
    paper.open_access_url && { href: paper.open_access_url, label: "Open access copy" },
    paper.openalex_id && { href: `https://openalex.org/${paper.openalex_id}`, label: "OpenAlex record" },
  ].filter(Boolean) as { href: string; label: string }[];
  return (
    <div className="min-h-0 flex-1 overflow-y-auto px-4 py-3">
      <button type="button" onClick={onBack} className="mb-2 flex items-center gap-1 rounded-lg px-1.5 py-1 font-headline text-xs text-ink-2 hover:bg-muted hover:text-ink">
        <ArrowLeft size={14} /> {hit ? "Back to the matches" : "All papers"}
      </button>
      <h3 className="text-base font-semibold leading-snug text-ink">{paper.resolved ? paper.title : "Unidentified paper"}</h3>
      {paper.resolved ? (
        <>
          <p className="mt-1.5 text-sm text-ink-2">
            {(paper.authors ?? []).join(", ")}
            {paper.author_count && paper.author_count > (paper.authors ?? []).length ? `, and ${paper.author_count - (paper.authors ?? []).length} more` : ""}
          </p>
          <p className="mt-1 font-headline text-xs text-ink-2">
            {[
              [paper.venue, paper.volume && `${paper.volume}${paper.issue ? `(${paper.issue})` : ""}`, paper.page_range].filter(Boolean).join(" "),
              paper.year,
              paper.type,
            ]
              .filter(Boolean)
              .join(" · ")}
            {typeof paper.cited_by_count === "number" ? ` · cited ${paper.cited_by_count} times (OpenAlex)` : ""}
          </p>
          <ul className="mt-2 flex flex-wrap gap-x-3 gap-y-1">
            {links.map((link) => (
              <li key={link.href}>
                <a href={link.href} target="_blank" rel="noreferrer" className="flex items-center gap-1 font-headline text-xs font-medium text-brand-ink underline-offset-2 hover:underline">
                  <ExternalLink size={12} /> {link.label}
                </a>
              </li>
            ))}
          </ul>
        </>
      ) : (
        <p className="mt-1.5 text-sm text-ink-2">{paper.reason}</p>
      )}
      <button
        type="button"
        onClick={() => onAsk(paper)}
        className="mt-3 flex items-center gap-2 rounded-lg bg-brand px-3 py-2 text-sm font-medium text-on-brand hover:opacity-90"
      >
        <MessageSquareText size={15} /> Ask DISSOLVE about this paper
      </button>
      {hit && hit.passages.length > 0 && (
        <section className="mt-4">
          <h4 className="font-headline text-xs font-semibold uppercase tracking-wide text-ink-2">Matching passages</h4>
          <ol className="mt-2 space-y-2">
            {hit.passages.map((passage) => (
              <li key={passage.chunk_id} className="rounded-lg bg-muted p-2.5 text-xs leading-relaxed text-ink">
                <p className="mb-1 font-headline text-[11px] text-ink-2">
                  {passage.page ? `Page ${passage.page}` : "Page unknown"}
                  {passage.section ? ` · ${passage.section}` : ""} · rank {passage.rank}
                </p>
                {passage.excerpt}
              </li>
            ))}
          </ol>
        </section>
      )}
      <section className="mt-4">
        <h4 className="font-headline text-xs font-semibold uppercase tracking-wide text-ink-2">Abstract</h4>
        <p className="mt-1.5 whitespace-pre-line text-sm leading-relaxed text-ink">
          {paper.abstract ?? "OpenAlex has no abstract for this paper. The DOI link opens it at the publisher."}
        </p>
      </section>
      <p className="mt-4 font-headline text-[11px] text-ink-2">
        {paper.passages} passages of this paper are in the corpus{paper.pages ? `, from ${paper.pages} pages` : ""}.
      </p>
    </div>
  );
}
