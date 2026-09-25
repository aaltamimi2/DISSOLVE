// The DISSOLVE web API (src/dissolve/web.py). A turn streams newline-delimited JSON events.

export type SessionState = {
  session_id: string;
  model: string;
  model_label: string;
  model_ready: boolean;
  mode: string;
  contaminant: string;
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
export type Health = { ok: boolean; release: string; ui_built: boolean; features: Features };
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

export type TurnEvent =
  | { event: "turn.started"; text: string }
  | ({ event: "tool" } & ToolCall)
  | { event: "command.output"; command: string; text: string; state: SessionState }
  | {
      event: "turn.completed";
      status: string;
      answer: string;
      tool_calls: number;
      elapsed_s: number;
      state: SessionState;
    }
  | { event: "error"; message: string };

async function json<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, init);
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error((body as { detail?: string }).detail || `${response.status} ${response.statusText}`);
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
  session: (id: string) => json<SessionState & { messages: StoredMessage[] }>(`/api/sessions/${id}`),
  newSession: (model?: string) => json<SessionState>("/api/sessions", post(model ? { model } : {})),

  /** Run one message or slash command; onEvent sees each event as it arrives. */
  async turn(sessionId: string, text: string, onEvent: (event: TurnEvent) => void): Promise<void> {
    const response = await fetch(`/api/sessions/${sessionId}/turns`, post({ text }));
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
