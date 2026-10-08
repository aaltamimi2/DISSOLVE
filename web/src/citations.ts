// Literature citations in an answer. The agent cites a corpus passage by its ID, T5-<the first 12 hex digits of the
// paper's PDF hash>-<passage number>, which reads as noise. An answer is drawn with each cited passage as a numbered
// chip that names the paper on hover, and a list of its sources; the stored answer keeps the IDs.
import type { Paper } from "./api";

/** One cited passage: its number in the answer, its ID and the paper it belongs to (null when unknown). */
export type Cited = { n: number; id: string; hex: string; passage: number | null; paper: Paper | null; label: string };

// A bracketed group ([T5-…], [T5-…, T5-…]) or a bare ID; ONE reads the IDs out of either.
const CITATION =
  /\[\s*T5-[0-9a-f]{12}(?:-\d+)?(?:\s*[,;]\s*T5-[0-9a-f]{12}(?:-\d+)?)*\s*\]|(?<![\w-])T5-[0-9a-f]{12}(?:-\d+)?(?![\w-])/g;
const ONE = /T5-([0-9a-f]{12})(?:-(\d+))?/g;
const CODE = /(```[\s\S]*?(?:```|$)|`[^`\n]+`)/;

/** "Sánchez-Rivera et al., 2025": how a question names a paper. */
export function shortCitation(paper: Paper): string {
  const authors = paper.authors ?? [];
  const surname = (name: string) => name.trim().split(/\s+/).pop() ?? name;
  const who = authors.length > 2 ? `${surname(authors[0])} et al.` : authors.map(surname).join(" and ");
  return [who, paper.year].filter(Boolean).join(", ");
}

/** "Kim et al., 2026, passage 36"; a passage of an unknown paper keeps its ID. */
export function citationLabel(paper: Paper | null, id: string, passage: number | null): string {
  if (!paper?.resolved) return `Literature passage ${id}`;
  return passage === null ? shortCitation(paper) : `${shortCitation(paper)}, passage ${passage}`;
}

/** The answer's Markdown with each cited passage as a link #cite-N-HEX (numbered by first appearance, titled with its
 * label), and the sources in that order. Code is left alone. */
export function citeAnswer(text: string, papers: Paper[] | null): { markdown: string; sources: Cited[] } {
  const sources: Cited[] = [];
  const byId = new Map<string, Cited>();
  const chip = (id: string, hex: string, passage: string | undefined) => {
    let source = byId.get(id);
    if (!source) {
      const paper = papers?.find((p) => p.sha256.startsWith(hex)) ?? null;
      const number = passage === undefined ? null : Number(passage);
      source = { n: sources.length + 1, id, hex, passage: number, paper, label: citationLabel(paper, id, number) };
      byId.set(id, source);
      sources.push(source);
    }
    return `[${source.n}](#cite-${source.n}-${hex} "${source.label.replace(/["\\]/g, "")}")`;
  };
  const parts = text.split(CODE);
  for (let k = 0; k < parts.length; k += 2) {
    parts[k] = parts[k].replace(CITATION, (match) => Array.from(match.matchAll(ONE), (m) => chip(m[0], m[1], m[2])).join(""));
  }
  return { markdown: parts.join(""), sources };
}

/** The paper a chip's link (#cite-N-HEX) points to, as its hash prefix, or null for any other link. */
export function citedHex(href: string | undefined): string | null {
  return href?.match(/^#cite-\d+-([0-9a-f]{12})$/)?.[1] ?? null;
}

/** The sources by paper: one entry per paper with the numbers of its chips and its passages, in first-cited order; a
 * passage of an unknown paper stays an entry of its own. */
export function sourcesByPaper(sources: Cited[]): { key: string; hex: string; numbers: number[]; paper: Paper | null; label: string }[] {
  const groups = new Map<string, { key: string; hex: string; numbers: number[]; paper: Paper | null; passages: number[]; label: string }>();
  for (const source of sources) {
    const key = source.paper?.resolved ? source.paper.sha256 : source.id;
    const group = groups.get(key) ?? { key, hex: source.hex, numbers: [], paper: source.paper, passages: [], label: source.label };
    group.numbers.push(source.n);
    if (source.passage !== null && !group.passages.includes(source.passage)) group.passages.push(source.passage);
    groups.set(key, group);
  }
  return Array.from(groups.values(), ({ passages, ...group }) => {
    if (!group.paper?.resolved) return group;
    const where = passages.length === 0 ? "" : `, passage${passages.length > 1 ? "s" : ""} ${passages.join(", ")}`;
    return { ...group, label: shortCitation(group.paper) + where };
  });
}
