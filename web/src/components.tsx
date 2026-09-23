import {
  AlertCircle,
  Check,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  CircleDashed,
  Copy,
  Download,
  FlaskConical,
  Loader2,
  MessageSquarePlus,
  Moon,
  PanelLeft,
  RefreshCw,
  Send,
  Sun,
  TerminalSquare,
  X,
  XCircle,
} from "lucide-react";
import { useEffect, useMemo, useRef, useState, type KeyboardEvent, type ReactNode, type RefObject } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import type { Command, Doctor, Features, Model, SessionRow, SessionState, ToolCall } from "./api";
import { family, MODE_CHIPS, offeredActions, type Example, type QuickAction } from "./content";

export type ChatMessage =
  | { id: string; role: "user"; text: string }
  | { id: string; role: "assistant"; text: string; tools: ToolCall[]; status?: string | null; elapsed?: number; running?: boolean }
  | { id: string; role: "command"; command: string; text: string }
  | { id: string; role: "error"; text: string };

const cx = (...parts: (string | false | null | undefined)[]) => parts.filter(Boolean).join(" ");

export function BrandMark({ size = 40 }: { size?: number }) {
  return (
    <div
      className="flex shrink-0 items-center justify-center rounded-xl shadow-soft"
      style={{ width: size, height: size, background: "linear-gradient(135deg, var(--primary) 0%, var(--primary-hover) 100%)" }}
    >
      <FlaskConical size={size * 0.55} color="white" strokeWidth={2} aria-hidden />
    </div>
  );
}

function IconButton({ label, onClick, children, active }: { label: string; onClick: () => void; children: ReactNode; active?: boolean }) {
  return (
    <button
      type="button"
      onClick={onClick}
      title={label}
      aria-label={label}
      className={cx(
        "flex h-9 items-center gap-2 rounded-lg px-2.5 text-sm font-medium transition-colors",
        active ? "bg-brand text-white" : "bg-muted text-ink hover:bg-line",
      )}
    >
      {children}
    </button>
  );
}

export function StatusBadge({ doctor, onClick }: { doctor: Doctor | null; onClick: () => void }) {
  const failing = doctor?.checks.filter((c) => c.status === "fail") ?? [];
  const ready = doctor?.ready ?? false;
  return (
    <button
      type="button"
      onClick={onClick}
      title={doctor ? (ready ? "All checks pass" : failing.map((c) => c.name).join(", ")) : "Checking the environment"}
      className="flex h-9 items-center gap-1.5 rounded-full px-3 text-sm font-medium"
      style={{
        backgroundColor: !doctor ? "var(--bg-tertiary)" : ready ? "rgba(16, 185, 129, 0.15)" : "rgba(245, 158, 11, 0.15)",
        color: !doctor ? "var(--text-secondary)" : ready ? "var(--success)" : "var(--warning)",
      }}
    >
      {!doctor ? <Loader2 size={14} className="animate-spin" /> : ready ? <CheckCircle2 size={14} /> : <AlertCircle size={14} />}
      {!doctor ? "Checking" : ready ? "Ready" : `Limited · ${failing.length}`}
    </button>
  );
}

export function Header(props: {
  doctor: Doctor | null;
  models: Model[];
  model: string;
  theme: "light" | "dark";
  canExport: boolean;
  onToggleSidebar: () => void;
  onShowSystem: () => void;
  onModel: (alias: string) => void;
  onTheme: () => void;
  onExport: () => void;
  onNewChat: () => void;
}) {
  return (
    <header className="z-20 shrink-0 border-b border-line bg-surface/90 backdrop-blur-sm">
      <div className="flex items-center justify-between gap-3 px-3 py-2.5 sm:px-4">
        <div className="flex min-w-0 items-center gap-3">
          <IconButton label="Toggle conversations and system panel" onClick={props.onToggleSidebar}>
            <PanelLeft size={18} />
          </IconButton>
          <BrandMark size={38} />
          <div className="min-w-0 leading-tight">
            <h1 className="whitespace-nowrap font-headline text-lg font-semibold tracking-tight text-ink">
              DISSOLVE<span className="hidden sm:inline"> Agent</span>
            </h1>
            <p className="hidden truncate font-headline text-xs text-ink-3 sm:block">Advanced polymer separation engineering</p>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <StatusBadge doctor={props.doctor} onClick={props.onShowSystem} />
          <label className="hidden md:block">
            <span className="sr-only">Model</span>
            <select
              value={props.model}
              onChange={(e) => props.onModel(e.target.value)}
              className="h-9 cursor-pointer rounded-lg border border-line bg-muted px-2.5 text-sm text-ink"
              title="The language model that drives the agent"
            >
              {props.models.map((m) => (
                <option key={m.alias} value={m.alias} disabled={!m.ready}>
                  {m.label}
                  {m.ready ? "" : " (no key)"}
                </option>
              ))}
            </select>
          </label>
          <IconButton label={`Switch to ${props.theme === "light" ? "dark" : "light"} mode`} onClick={props.onTheme}>
            {props.theme === "light" ? <Moon size={17} /> : <Sun size={17} />}
          </IconButton>
          {props.canExport && (
            <IconButton label="Export this conversation as Markdown" onClick={props.onExport}>
              <Download size={17} />
              <span className="hidden lg:inline">Export</span>
            </IconButton>
          )}
          <IconButton label="New conversation" onClick={props.onNewChat}>
            <MessageSquarePlus size={17} />
            <span className="hidden lg:inline">New</span>
          </IconButton>
        </div>
      </div>
    </header>
  );
}

function ago(iso: string | null): string {
  if (!iso) return "";
  const seconds = (Date.now() - new Date(iso).getTime()) / 1000;
  if (seconds < 60) return "just now";
  if (seconds < 3600) return `${Math.floor(seconds / 60)} min ago`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)} h ago`;
  return new Date(iso).toLocaleDateString();
}

const CHECK_ICON: Record<string, ReactNode> = {
  pass: <CheckCircle2 size={15} className="text-ok" />,
  fail: <XCircle size={15} className="text-bad" />,
  warn: <AlertCircle size={15} className="text-warn" />,
};

export function Sidebar(props: {
  open: boolean;
  tab: "conversations" | "system";
  onTab: (tab: "conversations" | "system") => void;
  onClose: () => void;
  sessions: SessionRow[];
  current: string | null;
  onOpenSession: (id: string) => void;
  doctor: Doctor | null;
  onRefreshDoctor: () => void;
}) {
  const [expanded, setExpanded] = useState<string | null>(null);
  if (!props.open) return null;
  return (
    <>
      <div className="fixed inset-0 z-30 bg-black/40 lg:hidden" onClick={props.onClose} aria-hidden />
      <aside className="fixed inset-y-0 left-0 z-40 flex w-[300px] flex-col border-r border-line bg-surface lg:static lg:z-auto">
        <div className="flex items-center gap-1 border-b border-line p-2">
          {(["conversations", "system"] as const).map((tab) => (
            <button
              key={tab}
              type="button"
              onClick={() => props.onTab(tab)}
              className={cx(
                "flex-1 rounded-lg px-3 py-1.5 text-sm font-medium capitalize",
                props.tab === tab ? "bg-canvas text-ink shadow-soft" : "text-ink-2 hover:text-ink",
              )}
            >
              {tab}
            </button>
          ))}
          <button type="button" onClick={props.onClose} aria-label="Close panel" className="rounded-lg p-1.5 text-ink-2 hover:bg-muted lg:hidden">
            <X size={18} />
          </button>
        </div>
        <div className="min-h-0 flex-1 overflow-y-auto p-2">
          {props.tab === "conversations" ? (
            props.sessions.length === 0 ? (
              <p className="px-3 py-6 text-center font-headline text-sm text-ink-3">No conversations yet.</p>
            ) : (
              <ul className="space-y-0.5">
                {props.sessions.map((row) => (
                  <li key={row.session_id}>
                    <button
                      type="button"
                      onClick={() => props.onOpenSession(row.session_id)}
                      className={cx(
                        "w-full rounded-lg px-3 py-2 text-left",
                        row.session_id === props.current ? "bg-brand-tint" : "hover:bg-muted",
                      )}
                    >
                      <span className="line-clamp-2 font-headline text-sm text-ink">{row.title || "Untitled conversation"}</span>
                      <span className="mt-0.5 block font-headline text-xs text-ink-3">
                        {ago(row.updated_at)} · {row.turns} turn{row.turns === 1 ? "" : "s"}
                      </span>
                    </button>
                  </li>
                ))}
              </ul>
            )
          ) : (
            <div className="space-y-1">
              <div className="flex items-center justify-between px-2 pb-2 pt-1">
                <span className="font-headline text-xs font-semibold uppercase tracking-wider text-ink-3">dissolve doctor</span>
                <button
                  type="button"
                  onClick={props.onRefreshDoctor}
                  className="flex items-center gap-1 rounded-md px-2 py-1 text-xs text-ink-2 hover:bg-muted"
                >
                  <RefreshCw size={12} /> Re-run
                </button>
              </div>
              {!props.doctor && (
                <p className="flex items-center gap-2 px-2 font-headline text-sm text-ink-2">
                  <Loader2 size={14} className="animate-spin" /> Checking…
                </p>
              )}
              {props.doctor?.checks.map((check) => (
                <button
                  key={check.name}
                  type="button"
                  onClick={() => setExpanded(expanded === check.name ? null : check.name)}
                  className="w-full rounded-lg px-2 py-2 text-left hover:bg-muted"
                >
                  <span className="flex items-center gap-2 font-headline text-sm text-ink">
                    {CHECK_ICON[check.status] ?? <CircleDashed size={15} className="text-ink-3" />}
                    <span className="flex-1">{check.name}</span>
                    <ChevronRight size={14} className={cx("text-ink-3 transition-transform", expanded === check.name && "rotate-90")} />
                  </span>
                  {expanded === check.name && (
                    <span className="mt-1.5 block whitespace-pre-wrap pl-6 font-mono text-[11px] leading-relaxed text-ink-2">{check.detail}</span>
                  )}
                </button>
              ))}
            </div>
          )}
        </div>
      </aside>
    </>
  );
}

function QuickActionCard({ action, onPick }: { action: QuickAction; onPick: (example: Example) => void }) {
  const key = `dissolve-example-${action.label.toLowerCase().replace(/[^a-z]+/g, "-")}`;
  const [shown, setShown] = useState(() => {
    const saved = Number(localStorage.getItem(key) ?? -1);
    return Number.isInteger(saved) && saved >= -1 && saved < action.examples.length ? saved : -1;
  });
  const Icon = action.icon;
  const count = action.examples.length;
  return (
    <button
      type="button"
      onClick={() => {
        const next = (shown + 1) % count;
        setShown(next);
        localStorage.setItem(key, String(next));
        onPick(action.examples[next]);
      }}
      className="group flex flex-col gap-1.5 rounded-xl border border-line bg-surface p-3.5 text-left shadow-soft transition-all hover:-translate-y-0.5 hover:border-brand-soft hover:shadow-lift"
      aria-label={`${action.label}: insert example ${((shown + 1) % count) + 1} of ${count}`}
    >
      <span className="flex items-center justify-between">
        <span className="flex h-8 w-8 items-center justify-center rounded-lg bg-brand-tint text-brand transition-colors group-hover:bg-brand group-hover:text-white">
          <Icon size={17} aria-hidden />
        </span>
        <span className="rounded-md bg-brand px-1.5 py-0.5 font-headline text-[11px] font-medium text-white">
          {Math.max(shown, 0) + 1}/{count}
        </span>
      </span>
      <span className="mt-1 font-headline text-sm font-semibold text-ink">{action.label}</span>
      <span className="font-headline text-xs leading-snug text-ink-2">{action.blurb}</span>
    </button>
  );
}

export function Welcome({ onPick, features }: { onPick: (example: Example) => void; features: Features }) {
  const topics = ["polymer solubility", "separation planning", "solvent safety", "contaminant removal"];
  if (features.tea) topics.push("techno-economics");
  if (features.literature) topics.push("the literature");
  return (
    <div className="rise mx-auto flex w-full max-w-5xl flex-col items-center px-4 pb-6 pt-10 text-center sm:pt-16">
      <BrandMark size={64} />
      <h2 className="mt-4 font-headline text-2xl font-semibold tracking-tight text-ink">DISSOLVE Agent</h2>
      <p className="mt-2 max-w-xl text-ink-2">Ask about {`${topics.slice(0, -1).join(", ")} and ${topics.at(-1)}`}.</p>
      <div className="mt-8 grid w-full grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4">
        {offeredActions(features).map((action) => (
          <QuickActionCard key={action.label} action={action} onPick={onPick} />
        ))}
      </div>
      <p className="mt-4 font-headline text-xs text-ink-3">
        Click a card to cycle through its examples · type <kbd className="rounded bg-muted px-1">/</kbd> for modes such as
        /contaminant and {features.literature ? "/literature" : "/solvents"}
      </p>
    </div>
  );
}

function ToolRow({ tool }: { tool: ToolCall }) {
  const fam = family(tool.name);
  const Icon = fam.icon;
  const args = tool.summary.slice(tool.name.length).replace(/ #[0-9a-f]+\)/, ")").replace(/ -> \d+ B$/, "");
  return (
    <li className="flex items-start gap-2.5 py-1.5">
      <span className="mt-0.5 flex h-6 w-6 shrink-0 items-center justify-center rounded-md" style={{ backgroundColor: fam.tint, color: fam.color }}>
        <Icon size={13} aria-hidden />
      </span>
      <span className="min-w-0 flex-1">
        <span className="flex flex-wrap items-center gap-x-2 gap-y-0.5">
          <span className="font-mono text-[12.5px] font-medium text-ink">{tool.name}</span>
          {tool.ok ? (
            <Check size={13} className="text-ok" aria-label="succeeded" />
          ) : (
            <span className="rounded bg-bad/10 px-1.5 font-mono text-[11px] text-bad">{tool.error_code || "refused"}</span>
          )}
          {tool.source_basis && <span className="font-mono text-[11px] text-ink-3">{tool.source_basis}</span>}
        </span>
        <span className="block truncate font-mono text-[11px] text-ink-3" title={args}>
          {args}
        </span>
        {tool.error && <span className="mt-0.5 block font-headline text-xs text-bad">{tool.error}</span>}
      </span>
    </li>
  );
}

export function ToolTrace({ tools, running }: { tools: ToolCall[]; running?: boolean }) {
  const [open, setOpen] = useState(false);
  const failed = tools.filter((t) => !t.ok).length;
  const families = [...new Map(tools.map((t) => [family(t.name).label, family(t.name)])).values()];
  if (!tools.length && !running) return null;
  const expanded = open || running;
  return (
    <div className="mb-3 rounded-xl border border-line bg-canvas/60">
      <button
        type="button"
        onClick={() => setOpen(!open)}
        className="flex w-full items-center gap-2 px-3 py-2 text-left"
        aria-expanded={expanded}
      >
        {running ? <Loader2 size={14} className="animate-spin text-brand" /> : <ChevronDown size={14} className={cx("text-ink-3 transition-transform", !open && "-rotate-90")} />}
        <span className="font-headline text-xs font-medium text-ink-2">
          {running
            ? tools.length
              ? `Working · ${tools.length} tool${tools.length === 1 ? "" : "s"} so far`
              : "Working…"
            : `Used ${tools.length} tool${tools.length === 1 ? "" : "s"}`}
          {failed ? ` · ${failed} refused` : ""}
        </span>
        <span className="ml-auto flex gap-1">
          {families.map((f) => (
            <span key={f.label} title={f.label} className="h-2 w-2 rounded-full" style={{ backgroundColor: f.color }} />
          ))}
        </span>
      </button>
      {expanded && tools.length > 0 && (
        <ul className="border-t border-line px-3 py-1">
          {tools.map((tool, i) => (
            <ToolRow key={i} tool={tool} />
          ))}
        </ul>
      )}
    </div>
  );
}

function Markdown({ text }: { text: string }) {
  return (
    <div className="markdown-content">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          a: ({ node: _node, children, ...rest }) => (
            <a {...rest} target="_blank" rel="noopener noreferrer">
              {children}
            </a>
          ),
          table: ({ node: _node, ...rest }) => (
            <div className="table-wrap">
              <table {...rest} />
            </div>
          ),
        }}
      >
        {text}
      </ReactMarkdown>
    </div>
  );
}

export function MessageView({ message, onCopy }: { message: ChatMessage; onCopy: (text: string) => void }) {
  if (message.role === "user") {
    return (
      <div className="rise flex justify-end">
        <div className="max-w-[85%] whitespace-pre-wrap rounded-2xl rounded-br-md bg-brand px-4 py-2.5 text-[0.95rem] text-white shadow-soft">
          {message.text}
        </div>
      </div>
    );
  }
  if (message.role === "command") {
    return (
      <div className="rise flex items-start gap-2 rounded-xl border border-dashed border-line px-3 py-2">
        <TerminalSquare size={15} className="mt-0.5 shrink-0 text-ink-3" />
        <div className="min-w-0">
          <span className="font-mono text-xs font-medium text-brand">{message.command}</span>
          <pre className="mt-0.5 overflow-x-auto whitespace-pre-wrap font-mono text-xs text-ink-2">{message.text || "done"}</pre>
        </div>
      </div>
    );
  }
  if (message.role === "error") {
    return (
      <div className="rise flex items-start gap-2 rounded-xl border border-bad/30 bg-bad/5 px-3.5 py-2.5 font-headline text-sm text-bad">
        <AlertCircle size={16} className="mt-0.5 shrink-0" />
        <span className="whitespace-pre-wrap">{message.text}</span>
      </div>
    );
  }
  return (
    <div className="rise flex gap-3">
      <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-muted text-ink">
        <FlaskConical size={16} aria-hidden />
      </div>
      <div className="min-w-0 flex-1">
        <div className="rounded-2xl rounded-tl-md border border-line bg-surface px-4 py-3 shadow-soft">
          <ToolTrace tools={message.tools} running={message.running} />
          {message.running && !message.text ? (
            <div className="typing-dots flex gap-1 py-1" aria-label="Working">
              <span />
              <span />
              <span />
            </div>
          ) : (
            <Markdown text={message.text} />
          )}
        </div>
        {!message.running && (
          <div className="mt-1.5 flex items-center gap-3 px-1 font-headline text-xs text-ink-3">
            {message.elapsed !== undefined && <span>{message.elapsed.toFixed(1)} s</span>}
            {message.status && message.status !== "ok" && <span className="text-warn">{message.status.replaceAll("_", " ")}</span>}
            <button type="button" onClick={() => onCopy(message.text)} className="flex items-center gap-1 rounded px-1 hover:text-ink">
              <Copy size={12} /> Copy
            </button>
          </div>
        )}
      </div>
    </div>
  );
}

type PaletteItem = { label: string; detail: string; insert: string; complete: boolean };

function palette(value: string, commands: Command[]): PaletteItem[] {
  if (!value.startsWith("/") || value.includes("\n")) return [];
  const [head, ...rest] = value.slice(1).split(" ");
  if (rest.length === 0) {
    return commands
      .filter((c) => c.command.startsWith(`/${head.toLowerCase()}`))
      .map((c) => ({ label: c.command, detail: c.summary, insert: c.options.length ? `${c.command} ` : c.command, complete: !c.options.length }));
  }
  const command = commands.find((c) => c.command === `/${head.toLowerCase()}`);
  const typed = rest.join(" ").toLowerCase();
  return (command?.options ?? [])
    .filter((o) => o.value.startsWith(typed))
    .map((o) => ({ label: `${command!.command} ${o.value}`, detail: o.description, insert: `${command!.command} ${o.value}`, complete: true }));
}

function ModeChip({
  label,
  value,
  command,
  onPick,
}: {
  label: string;
  value: string;
  command: Command | undefined;
  onPick: (text: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const close = (e: MouseEvent) => !ref.current?.contains(e.target as Node) && setOpen(false);
    const escape = (e: globalThis.KeyboardEvent) => e.key === "Escape" && setOpen(false);
    document.addEventListener("mousedown", close);
    document.addEventListener("keydown", escape);
    return () => {
      document.removeEventListener("mousedown", close);
      document.removeEventListener("keydown", escape);
    };
  }, [open]);
  const active = value !== "off";
  return (
    <div ref={ref} className="relative">
      <button
        type="button"
        onClick={() => setOpen(!open)}
        aria-expanded={open}
        title={command?.summary}
        className={cx(
          "flex items-center gap-1 whitespace-nowrap rounded-full border px-2.5 py-1 font-headline text-xs transition-colors",
          active && label !== "Assumptions" && label !== "Solvents" && label !== "Breadth"
            ? "border-brand/40 bg-brand-tint text-brand"
            : "border-line bg-canvas text-ink-2 hover:text-ink",
        )}
      >
        <span className="text-ink-3">{command?.command ?? label}</span>
        <span className="font-medium">{value}</span>
        <ChevronDown size={12} />
      </button>
      {open && command && (
        <div className="absolute bottom-full left-0 z-30 mb-2 w-80 overflow-hidden rounded-xl border border-line bg-elevated shadow-float">
          <p className="border-b border-line px-3 py-2 font-headline text-xs text-ink-3">{command.summary}</p>
          <ul className="max-h-72 overflow-y-auto py-1">
            {command.options.map((option) => (
              <li key={option.value}>
                <button
                  type="button"
                  onClick={() => {
                    setOpen(false);
                    onPick(`${command.command} ${option.value}`);
                  }}
                  className="flex w-full items-start gap-2 px-3 py-2 text-left hover:bg-muted"
                >
                  <Check size={14} className={cx("mt-0.5 shrink-0", option.value === value ? "text-brand" : "invisible")} />
                  <span>
                    <span className="block font-mono text-xs font-medium text-ink">{option.value}</span>
                    <span className="block font-headline text-xs text-ink-2">{option.description}</span>
                  </span>
                </button>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}

export function Composer(props: {
  value: string;
  onChange: (value: string) => void;
  onSend: (text: string) => void;
  busy: boolean;
  commands: Command[];
  state: SessionState | null;
  inputRef: RefObject<HTMLTextAreaElement | null>;
}) {
  const ref = props.inputRef;
  const [selected, setSelected] = useState(0);
  const [dismissed, setDismissed] = useState(false);
  const items = useMemo(() => (dismissed ? [] : palette(props.value, props.commands)), [dismissed, props.value, props.commands]);

  useEffect(() => {
    setSelected(0);
    setDismissed(false);
  }, [props.value]);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 220)}px`;
  }, [props.value]);
  useEffect(() => {
    if (!props.busy) ref.current?.focus();
  }, [props.busy]);

  // What the composer sends leaves it empty; a mode chip sends without touching a half-written message.
  const send = (text: string) => {
    if (!text.trim() || props.busy) return;
    props.onChange("");
    props.onSend(text.trim());
  };
  const choose = (item: PaletteItem) => {
    if (item.complete) send(item.insert);
    else props.onChange(item.insert);
  };
  const onKey = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (items.length) {
      if (e.key === "ArrowDown" || e.key === "ArrowUp") {
        e.preventDefault();
        setSelected((selected + (e.key === "ArrowDown" ? 1 : items.length - 1)) % items.length);
        return;
      }
      if (e.key === "Tab") {
        e.preventDefault();
        props.onChange(items[selected].insert);
        return;
      }
      if (e.key === "Escape") {
        setDismissed(true);
        return;
      }
      if (e.key === "Enter" && !e.shiftKey && items[selected].insert.trim() !== props.value.trim()) {
        e.preventDefault();
        choose(items[selected]);
        return;
      }
    }
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      send(props.value);
    }
  };

  const commandOf = (name: string) => props.commands.find((c) => c.command === name);
  return (
    <div className="shrink-0 border-t border-line bg-surface/90 px-3 pb-3 pt-2.5 backdrop-blur-sm sm:px-4">
      <div className="mx-auto w-full max-w-[860px]">
        {props.state && (
          <div className="mb-2 flex flex-wrap items-center gap-1.5">
            {MODE_CHIPS.filter((chip) => commandOf(chip.command)).map((chip) => (
              <ModeChip
                key={chip.command}
                label={chip.label}
                value={String(props.state?.[chip.state] ?? "")}
                command={commandOf(chip.command)}
                onPick={(text) => !props.busy && props.onSend(text)}
              />
            ))}
          </div>
        )}
        <div className="relative">
          {items.length > 0 && (
            <ul className="absolute bottom-full left-0 right-0 z-30 mb-2 max-h-80 overflow-y-auto rounded-xl border border-line bg-elevated py-1 shadow-float" role="listbox">
              {items.map((item, i) => (
                <li key={item.label} role="option" aria-selected={i === selected}>
                  <button
                    type="button"
                    onMouseDown={(e) => e.preventDefault()}
                    onClick={() => choose(item)}
                    onMouseEnter={() => setSelected(i)}
                    className={cx("flex w-full items-baseline gap-3 px-3 py-2 text-left", i === selected && "bg-muted")}
                  >
                    <span className="shrink-0 font-mono text-sm font-medium text-brand">{item.label}</span>
                    <span className="truncate font-headline text-xs text-ink-2">{item.detail}</span>
                  </button>
                </li>
              ))}
            </ul>
          )}
          <textarea
            ref={ref}
            rows={1}
            value={props.value}
            onChange={(e) => props.onChange(e.target.value)}
            onKeyDown={onKey}
            placeholder="Ask DISSOLVE… or type / for modes and commands"
            aria-label="Message"
            className="block max-h-[220px] min-h-[52px] w-full resize-none rounded-xl border border-line bg-canvas py-3.5 pl-4 pr-14 text-[0.95rem] text-ink shadow-soft outline-none placeholder:text-ink-3 focus:border-brand-soft"
          />
          <button
            type="button"
            onClick={() => send(props.value)}
            disabled={!props.value.trim() || props.busy}
            aria-label={props.busy ? "Working" : "Send"}
            className="absolute bottom-2 right-2 flex h-9 w-9 items-center justify-center rounded-lg bg-brand text-white transition-colors hover:bg-brand-hover disabled:cursor-not-allowed disabled:bg-muted disabled:text-ink-3"
          >
            {props.busy ? <Loader2 size={17} className="animate-spin" /> : <Send size={17} />}
          </button>
        </div>
        <p className="mt-1.5 hidden text-center font-mono text-[11px] text-ink-3 sm:block" aria-hidden>
          Enter to send · Shift+Enter for a new line · / for commands
        </p>
      </div>
    </div>
  );
}

export function Toast({ text, kind }: { text: string; kind: "info" | "error" }) {
  return (
    <div
      role="status"
      className="rise fixed right-4 top-16 z-50 rounded-lg px-4 py-2 font-headline text-sm text-white shadow-float"
      style={{ backgroundColor: kind === "error" ? "var(--error)" : "var(--primary)" }}
    >
      {text}
    </div>
  );
}
