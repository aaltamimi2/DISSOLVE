// A table whose header names a SMILES column gains a Structure column just before it: the server's RDKit drawing of
// each row's SMILES (/api/structure.svg). It applies to any answer, since the agent only writes SMILES and the page
// draws them. Before, not after: a long SMILES pushed a drawing after it off a phone's screen.
import type { Element, ElementContent, Root, RootContent } from "hast";

export const STRUCTURE_URL = "/api/structure.svg?smiles=";

const text = (node: Root | RootContent | ElementContent): string =>
  node.type === "text" ? node.value : "children" in node ? node.children.map(text).join("") : "";

const cellsOf = (row: Element): Element[] =>
  row.children.filter((c): c is Element => c.type === "element" && (c.tagName === "td" || c.tagName === "th"));

function addStructureColumn(table: Element) {
  const rows = table.children
    .filter((c): c is Element => c.type === "element")
    .flatMap((part) => (part.tagName === "tr" ? [part] : part.children.filter((r): r is Element => r.type === "element" && r.tagName === "tr")));
  const header = rows[0];
  if (!header) return;
  const titles = cellsOf(header).map((cell) => text(cell).trim());
  const at = titles.findIndex((title) => /\bsmiles\b/i.test(title));
  if (at < 0 || titles.some((title) => /^structures?$/i.test(title))) return;
  const nameAt = at === 0 ? -1 : 0;
  rows.forEach((row, i) => {
    const cells = cellsOf(row);
    let added: Element;
    if (i === 0) {
      added = { type: "element", tagName: "th", properties: {}, children: [{ type: "text", value: "Structure" }] };
    } else {
      const smiles = cells[at] ? text(cells[at]).trim().replace(/^`+|`+$/g, "") : "";
      const name = nameAt >= 0 && cells[nameAt] ? text(cells[nameAt]).trim() : "";
      const image: Element = {
        type: "element",
        tagName: "img",
        properties: { src: STRUCTURE_URL + encodeURIComponent(smiles), alt: smiles, title: name || smiles },
        children: [],
      };
      added = { type: "element", tagName: "td", properties: {}, children: smiles ? [image] : [] };
    }
    row.children = [...cells.slice(0, at), added, ...cells.slice(at)];
  });
}

/** The rehype plugin: every table in the answer gets the treatment. */
export function rehypeStructures() {
  return (tree: Root) => {
    const visit = (node: Root | ElementContent) => {
      if (node.type === "element" && node.tagName === "table") addStructureColumn(node);
      if ("children" in node) node.children.forEach((child) => visit(child as ElementContent));
    };
    visit(tree);
  };
}
