// The DISSOLVE web API (src/dissolve/web.py). A turn streams newline-delimited JSON events.

export type SessionState = {
  session_id: string;
  model: string;
  model_label: string;
  model_ready: boolean;
  mode: string;
  literature: string;
  solvents: string;
  breadth: string;
};

export type ToolCall = {
  name: string;
  summary: string;
  ok: boolean;
  source_basis?: string | null;
  error_code?: string | null;
  error?: string | null;
};

export type StoredMessage =
  | { role: "user"; text: string }
  | { role: "assistant"; text: string; status?: string | null; tools: ToolCall[] };

export type Model = { alias: string; label: string; usage: string; key: string; ready: boolean; default: boolean };
export type CommandOption = { value: string; description: string };
export type Command = { command: string; summary: string; state?: keyof SessionState; options: CommandOption[] };
export type SessionRow = { session_id: string; updated_at: string | null; title: string | null; turns: number; model: string | null };
export type DoctorCheck = { name: string; status: "pass" | "warn" | "fail" | "not_required" | string; detail: string };
export type Doctor = { ready: boolean; checks: DoctorCheck[] };
/** What this deployment offers; a small host switches literature and live TEA off. */
export type Features = { literature: boolean; tea: boolean };
export type Health = { ok: boolean; release: string; ui_built: boolean; features: Features; contaminants_computed?: number | null };
/** A contaminant family a question can name instead of its members; `term` is how a question says it. */
export type ContaminantFamily = {
  name: string;
  term: string;
  description: string;
  aliases: string[];
  examples: string[];
  count: number;
  members: string[];
  source: "plastchem" | "workbook";
};

/** A paper of the literature corpus. Its record comes from OpenAlex, confirmed against the paper's own text; an
 * unresolved paper has only its passages and the reason. */
export type Paper = {
  document_id: string;
  sha256: string;
  passages: number;
  pages: number | null;
  resolved: boolean;
  reason?: string;
  title?: string;
  authors?: string[];
  author_count?: number;
  year?: number | null;
  date?: string | null;
  venue?: string | null;
  volume?: string | null;
  issue?: string | null;
  page_range?: string | null;
  type?: string | null;
  doi?: string | null;
  openalex_id?: string | null;
  cited_by_count?: number | null;
  open_access_url?: string | null;
  abstract?: string | null;
};
export type PaperList = { count: number; resolved: number; built_at: string | null; papers: Paper[] };
export type PaperPassage = { chunk_id: string; rank: number; page: string | number | null; section: string | null; excerpt: string | null };
/** A paper whose text matches a search, with its best passages in the order the literature search ranks them. */
export type PaperHit = { paper: Paper; best_rank: number; passages: PaperPassage[] };
export type PaperSearch = { query: string; depth: number; passages_ranked: number; results: PaperHit[] };

/** A server with a database has accounts; `access_code` says whether sign-up asks for the site's code. */
export type AuthConfig = { accounts: boolean; access_code: boolean; admin_reads?: boolean };
export type Me = { username: string | null; created_at?: string };

/** Fired when the server answers 401: the sign-in has expired, or was signed out elsewhere. */
export const SIGNED_OUT = "dissolve:signed-out";

/** One field of the TEA panel, as the CLI's process sheet lists it; a fraction is shown and typed as a percent. */
export type TeaField = {
  name: string;
  label: string;
  group: string;
  kind: "text" | "number" | "choice" | "bool" | "pair" | "list";
  unit: string;
  percent: boolean;
  hint: string;
  required: boolean;
  only_c1_c3: boolean;
  editable: boolean;
  rangeable: boolean;
  options?: { value: string; label: string }[];
};
export type TeaValue = number | string | boolean | null | number[];
/** A field's values across plants: from, to and step; from, to and a number of log-spaced points; or a list. */
export type TeaRange =
  | { kind: "linear"; from: number; to: number; step: number }
  | { kind: "log"; from: number; to: number; points: number }
  | { kind: "list"; values: (number | string | boolean)[] };
export type TeaSheet = {
  id: string;
  mode: "evaluate" | "sensitivity" | "route";
  opened?: "reference" | "previous"; // the TEA sheet button opened it, with the reference plant or the last run
  solvent_exclusions?: Record<string, string[]>; // solvents live TEA cannot build with a polymer (PS: Styrene)
  title: string;
  groups: string[];
  fields: TeaField[];
  values: Record<string, TeaValue>;
  origin: Record<string, string>;
  defaults: Record<string, TeaValue>;
  ranges: Record<string, TeaRange>;
  paired: { fields: string[]; rows: TeaValue[][] } | null;
  extras: Record<string, unknown>;
  editable: string[];
  route: { handle: string; row_id: unknown; steps: { polymer: string | null; solvent: string | null; temperature_c: number | null }[] } | null;
  sensitivity: { parameter: string; values_named: boolean } | null;
  polymers: string[];
  solvents: { name: string; price_usd_per_kg: number }[];
  limits: { max_plants: number; confirm_above: number; seconds_per_live_plant: number };
  expires_at: number;
};
/** What a run would do, checked by the server before anything runs. */
export type TeaCheck = {
  plants: number;
  stored: number;
  live: number;
  seconds: number;
  invalid: { label: string; error: string; error_code?: string | null }[];
  invalid_count: number;
  confirm: boolean;
  runnable: boolean;
  admitted_price?: number | null;
  error?: string;
};
export type TeaRow = {
  index: number;
  label: string;
  success: boolean;
  values: Record<string, TeaValue>;
  polymer?: string | null;
  solvent?: string | null;
  engine_mode?: string | null;
  stored_record?: string | null;
  can_cite_as_validated_process?: boolean | null;
  msp_usd_per_kg?: number | null;
  tci_usd?: number | null;
  aoc_usd_per_yr?: number | null;
  gwp_kg_co2e_per_kg?: number | null;
  electricity_mj_per_kg?: number | null;
  heating_mj_per_kg?: number | null;
  cooling_mj_per_kg?: number | null;
  total_energy_mj_per_kg?: number | null;
  error?: string;
  error_code?: string | null;
};
export type TeaResult = {
  sheet_id: string;
  mode: string;
  title: string;
  rows: TeaRow[];
  axes: { name: string; values: TeaValue[] }[];
  paired_fields: string[];
  planned: number;
  ran: number;
  stopped: boolean;
  handle?: string | null;
  source_basis?: string | null;
  edited_fields: string[];
  defaulted_fields: string[];
  fields: { name: string; label: string; unit: string; percent: boolean; kind: string }[];
  labels: Record<string, string>;
};
export type TeaProgress = { sheet_id: string; done: number; total: number; rows: TeaRow[]; running: string | null };
export type TeaState = { sheet: TeaSheet | null; progress: TeaProgress | null; result: TeaResult | null };
export type TeaAnswer = {
  sheet_id: string;
  action: "run" | "cancel" | "stop";
  values?: Record<string, TeaValue>;
  ranges?: Record<string, TeaRange>;
  drop?: number[];
};

export type TurnEvent =
  | { event: "turn.started"; text: string }
  | ({ event: "tool" } & ToolCall)
  | { event: "command.output"; command: string; text: string; state: SessionState }
  | { event: "tea.sheet"; sheet: TeaSheet }
  | { event: "tea.progress"; sheet_id: string; done: number; total: number; running?: string; rows?: TeaRow[] }
  | ({ event: "tea.result" } & TeaResult)
  | { event: "tea.closed"; sheet_id: string; reason: string; message?: string }
  | { event: "tea.waiting"; sheet_id: string }
  | {
      event: "turn.completed";
      status: string;
      answer: string;
      tool_calls: number;
      elapsed_s: number;
      state: SessionState;
    }
  | { event: "error"; message: string };

/** An API refusal; `detail` keeps a structured reason (the TEA panel's refused plants) when the server sent one. */
export class ApiError extends Error {
  status: number;
  detail: unknown;
  constructor(message: string, status: number, detail: unknown) {
    super(message);
    this.status = status;
    this.detail = detail;
  }
}

async function json<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, init);
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    if (response.status === 401 && !path.startsWith("/api/auth/")) window.dispatchEvent(new Event(SIGNED_OUT));
    const detail = (body as { detail?: unknown }).detail;
    const message = typeof detail === "string" ? detail : (detail as { message?: string } | undefined)?.message;
    throw new ApiError(message || `${response.status} ${response.statusText}`, response.status, detail);
  }
  return response.json() as Promise<T>;
}

const post = (body: unknown): RequestInit => ({
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify(body),
});

export const api = {
  health: () => json<Health>("/api/health"),
  doctor: (refresh = false) => json<Doctor>(`/api/doctor${refresh ? "?refresh=true" : ""}`),
  models: () => json<Model[]>("/api/models"),
  commands: () => json<Command[]>("/api/commands"),
  sessions: () => json<SessionRow[]>("/api/sessions"),
  contaminantFamilies: () => json<ContaminantFamily[]>("/api/contaminant-families"),
  papers: () => json<PaperList>("/api/literature/papers"),
  searchPapers: (query: string) => json<PaperSearch>(`/api/literature/search?q=${encodeURIComponent(query)}`),
  authConfig: () => json<AuthConfig>("/api/auth/config"),
  me: () => json<Me>("/api/auth/me"),
  login: (username: string, password: string) => json<Me>("/api/auth/login", post({ username, password })),
  signup: (username: string, password: string, access_code: string) =>
    json<Me>("/api/auth/signup", post({ username, password, access_code })),
  logout: () => json<{ ok: boolean }>("/api/auth/logout", post({})),
  session: (id: string) => json<SessionState & { messages: StoredMessage[] }>(`/api/sessions/${id}`),
  deleteSession: (id: string) => json<{ deleted: string }>(`/api/sessions/${id}`, { method: "DELETE" }),
  newSession: (model?: string) => json<SessionState>("/api/sessions", post(model ? { model } : {})),
  /** Report a problem with one of your answers; the site's admin reads it beside the turn it is about. */
  report: (id: string, question: string, note: string) =>
    json<{ report_id: string; saved: boolean }>(`/api/sessions/${id}/reports`, post({ question, note })),
  teaState: (id: string) => json<TeaState>(`/api/sessions/${id}/tea-sheet`),
  teaCheck: (id: string, body: Omit<TeaAnswer, "action">) => json<TeaCheck>(`/api/sessions/${id}/tea-sheet/check`, post(body)),
  teaAnswer: (id: string, body: TeaAnswer) => json<{ ok: boolean; action: string; plants?: number }>(`/api/sessions/${id}/tea-sheet`, post(body)),

  /** Run one message or slash command; onEvent sees each event as it arrives. */
  async turn(sessionId: string, text: string, onEvent: (event: TurnEvent) => void): Promise<void> {
    const response = await fetch(`/api/sessions/${sessionId}/turns`, post({ text }));
    if (response.status === 401) window.dispatchEvent(new Event(SIGNED_OUT));
    if (!response.ok || !response.body) {
      const body = await response.json().catch(() => ({}));
      throw new Error((body as { detail?: string }).detail || `${response.status} ${response.statusText}`);
    }
    const reader = response.body.pipeThrough(new TextDecoderStream()).getReader();
    let buffer = "";
    for (;;) {
      const { value, done } = await reader.read();
      buffer += value ?? "";
      const lines = buffer.split("\n");
      buffer = done ? "" : (lines.pop() ?? "");
      for (const line of lines) if (line.trim()) onEvent(JSON.parse(line) as TurnEvent);
      if (done) return;
    }
  },
};
