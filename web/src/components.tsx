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
  LogOut,
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
import agentLogo from "./assets/dissolve-agent.svg";
import {
  Component,
  useEffect,
  useId,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type ErrorInfo,
  type FormEvent,
  type KeyboardEvent,
  type ReactNode,
  type RefObject,
} from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { api, type Command, type ContaminantFamily, type Doctor, type Features, type Model, type SessionRow, type SessionState, type ToolCall } from "./api";
import { family, MODE_CHIPS, offeredActions, type Example, type QuickAction } from "./content";

export type ChatMessage =
  | { id: string; role: "user"; text: string }
  | { id: string; role: "assistant"; text: string; tools: ToolCall[]; status?: string | null; elapsed?: number; running?: boolean }
  | { id: string; role: "command"; command: string; text: string }
  | { id: string; role: "error"; text: string };

const cx = (...parts: (string | false | null | undefined)[]) => parts.filter(Boolean).join(" ");

/** The DISSOLVE agent: the logo's robot without its wordmark, `size` pixels tall. */
export function BrandMark({ size = 40 }: { size?: number }) {
  return <img src={agentLogo} alt="DISSOLVE agent" draggable={false} className="shrink-0 select-none" style={{ height: size, width: "auto" }} />;
}

function Field(props: {
  label: string;
  hint?: string;
  value: string;
  onChange: (value: string) => void;
  type?: string;
  autoComplete: string;
  autoFocus?: boolean;
}) {
  const id = useId();
  return (
    <div>
      <div className="flex items-baseline justify-between gap-3">
        <label htmlFor={id} className="font-headline text-xs font-medium text-ink-2">
          {props.label}
        </label>
        {props.hint && (
          <span id={`${id}-hint`} className="font-headline text-xs text-ink-2">
            {props.hint}
          </span>
        )}
      </div>
      <input
        id={id}
        aria-describedby={props.hint ? `${id}-hint` : undefined}
        type={props.type ?? "text"}
        value={props.value}
        onChange={(e) => props.onChange(e.target.value)}
        autoComplete={props.autoComplete}
        autoFocus={props.autoFocus}
        required
        spellCheck={false}
        className="mt-1 w-full rounded-lg border border-line bg-canvas px-3 py-2 text-[0.95rem] text-ink outline-none focus:border-brand-soft"
      />
    </div>
  );
}

/** A server with accounts opens here: sign in with a username and a password, or create an account, which asks for
 * the site's access code when the server has one. Nothing else is asked, and there is no email. */
export function SignIn({ accessCode, onSignedIn }: { accessCode: boolean; onSignedIn: (username: string) => void }) {
  const [mode, setMode] = useState<"in" | "up">("in");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [code, setCode] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [theme, setTheme] = useState<"light" | "dark">(() => {
    try {
      return localStorage.getItem("dissolve-theme") === "dark" ? "dark" : "light";
    } catch {
      return "light";
    }
  });
  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    try {
      localStorage.setItem("dissolve-theme", theme);
    } catch {
      /* private mode: the theme simply does not persist */
    }
  }, [theme]);
  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (mode === "up" && password !== confirm) {
      setError("The two passwords are not the same.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const me = mode === "in" ? await api.login(username, password) : await api.signup(username, password, code);
      onSignedIn(me.username ?? username);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  };
  const choose = (next: "in" | "up") => {
    setMode(next);
    setError(null);
  };
  return (
    <div className="relative flex min-h-dvh items-center justify-center bg-canvas px-4 py-10 text-ink">
      <div className="absolute right-4 top-4">
        <IconButton label={`Switch to ${theme === "light" ? "dark" : "light"} mode`} onClick={() => setTheme(theme === "light" ? "dark" : "light")}>
          {theme === "light" ? <Moon size={17} /> : <Sun size={17} />}
        </IconButton>
      </div>
      <main className="rise w-full max-w-sm">
        <div className="flex flex-col items-center text-center">
          <BrandMark size={76} />
          <h1 className="mt-4 font-headline text-2xl font-semibold tracking-tight text-ink">DISSOLVE Agent</h1>
          <p className="mt-1 font-headline text-sm text-ink-2">Advanced polymer separation engineering</p>
        </div>
        <form onSubmit={submit} className="mt-7 overflow-hidden rounded-2xl border border-line bg-surface shadow-soft">
          <div className="h-1 bg-brand" aria-hidden />
          <div className="p-5">
            <div role="tablist" aria-label="Sign in or create an account" className="grid grid-cols-2 gap-1 rounded-lg bg-muted p-1">
              {(["in", "up"] as const).map((m) => (
                <button
                  key={m}
                  type="button"
                  role="tab"
                  aria-selected={mode === m}
                  onClick={() => choose(m)}
                  className={cx(
                    "rounded-md py-1.5 font-headline text-sm font-medium transition-colors",
                    mode === m ? "bg-surface text-ink shadow-soft" : "text-ink-2 hover:text-ink",
                  )}
                >
                  {m === "in" ? "Sign in" : "Create account"}
                </button>
              ))}
            </div>
            <div className="mt-4 space-y-3">
              <Field label="Username" value={username} onChange={setUsername} autoComplete="username" autoFocus />
              <Field
                label="Password"
                hint={mode === "up" ? "at least 8 characters" : undefined}
                type="password"
                value={password}
                onChange={setPassword}
                autoComplete={mode === "in" ? "current-password" : "new-password"}
              />
              {mode === "up" && <Field label="Confirm password" type="password" value={confirm} onChange={setConfirm} autoComplete="new-password" />}
              {mode === "up" && accessCode && (
                <Field label="Access code" hint="the site password you were given" type="password" value={code} onChange={setCode} autoComplete="off" />
              )}
            </div>
            {error && (
              <p role="alert" className="mt-3 rounded-lg bg-bad/10 px-3 py-2 font-headline text-sm text-bad">
                {error}
              </p>
            )}
            <button
              type="submit"
              disabled={busy}
              className="mt-4 flex w-full items-center justify-center gap-2 rounded-lg bg-brand py-2.5 font-headline text-sm font-semibold text-on-brand transition-colors hover:bg-brand-hover disabled:opacity-60"
            >
              {busy && <Loader2 size={15} className="animate-spin" />}
              {mode === "in" ? "Sign in" : "Create account"}
            </button>
          </div>
        </form>
        <p className="mt-4 text-center font-headline text-xs text-ink-2">
          Your conversations are saved to your account and kept across updates.
        </p>
      </main>
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
        active ? "bg-brand text-on-brand" : "bg-muted text-ink hover:bg-line",
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
      <span className="hidden sm:inline">{!doctor ? "Checking" : ready ? "Ready" : `Limited · ${failing.length}`}</span>
    </button>
  );
}

/** Keeps a crash in the browser (a translator or extension rewriting the page, say) from leaving a blank page: it
 * says so, offers a reload, and sends the error to the server's log, the only place a hosted copy can see it. */
export class Boundary extends Component<{ children: ReactNode }, { error: Error | null }> {
  state: { error: Error | null } = { error: null };

  static getDerivedStateFromError(error: Error) {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    const report = { message: String(error.message), stack: error.stack?.slice(0, 1500), component: info.componentStack?.slice(0, 1500), agent: navigator.userAgent };
    fetch("/api/client-error", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(report), keepalive: true }).catch(() => undefined);
  }

  render() {
    const { error } = this.state;
    if (!error) return this.props.children;
    return (
      <div className="flex h-dvh items-center justify-center bg-canvas px-4 text-ink">
        <div className="w-full max-w-lg rounded-2xl border border-line bg-surface p-6 shadow-soft">
          <div className="flex items-center gap-3">
            <BrandMark size={40} />
            <h1 className="font-headline text-lg font-semibold">The page stopped drawing</h1>
          </div>
          <p className="mt-3 text-ink-2">
            Something in this browser interrupted the page. Your conversation is saved on the server, and reloading brings it
            back. If it happens again, try with page translation and extensions switched off for this site.
          </p>
          <pre className="mt-3 max-h-32 overflow-auto whitespace-pre-wrap rounded-lg bg-muted p-3 font-mono text-xs text-ink-2">{error.message}</pre>
          <button
            type="button"
            onClick={() => window.location.reload()}
            className="mt-4 flex items-center gap-2 rounded-lg bg-brand px-4 py-2 font-headline text-sm font-medium text-on-brand hover:bg-brand-hover"
          >
            <RefreshCw size={14} /> Reload
          </button>
        </div>
      </div>
    );
  }
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
  user?: string | null;
  onSignOut?: () => void;
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
          {props.user && props.onSignOut && (
            <>
              <span className="hidden max-w-[10rem] truncate pl-1 font-headline text-sm text-ink-2 md:inline" title={`Signed in as ${props.user}`}>
                {props.user}
              </span>
              <IconButton label={`Sign out ${props.user}`} onClick={props.onSignOut}>
                <LogOut size={17} />
              </IconButton>
            </>
          )}
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
        <span className="flex h-8 w-8 items-center justify-center rounded-lg bg-brand-tint text-brand-ink transition-colors group-hover:bg-brand group-hover:text-on-brand">
          <Icon size={17} aria-hidden />
        </span>
        <span className="rounded-md bg-brand px-1.5 py-0.5 font-headline text-[11px] font-medium text-on-brand">
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
      <p className="mt-5 font-headline text-[13px] text-ink-2">
        Click a card to cycle through its examples · type <kbd className="rounded border border-line bg-muted px-1.5 text-xs text-ink">/</kbd> for modes such as
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
        {running ? <Loader2 size={14} className="animate-spin text-brand-ink" /> : <ChevronDown size={14} className={cx("text-ink-3 transition-transform", !open && "-rotate-90")} />}
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
        <div className="max-w-[85%] whitespace-pre-wrap rounded-2xl rounded-br-md bg-navy px-4 py-2.5 text-[0.95rem] text-white shadow-soft">
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
          <span className="font-mono text-xs font-medium text-brand-ink">{message.command}</span>
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

/** `family` marks a family-name completion: Tab or a click takes it, and Enter still sends the message. */
type PaletteItem = { label: string; detail: string; insert: string; complete: boolean; family?: boolean };

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

/** How far left a chip's menu, at most `width` pixels wide, must move to stay on screen. At phone width a chip
 * wraps anywhere in its row, and a menu hanging past the right edge scrolled the whole page sideways. */
function useOnScreen(anchor: RefObject<HTMLElement | null>, open: boolean, width: number): number {
  const [shift, setShift] = useState(0);
  useLayoutEffect(() => {
    if (!open || !anchor.current) {
      setShift(0);
      return;
    }
    const left = anchor.current.getBoundingClientRect().left;
    setShift(Math.min(0, window.innerWidth - 16 - (left + Math.min(width, window.innerWidth - 32))));
  }, [anchor, open, width]);
  return shift;
}

/** Contaminant families whose name the message is in the middle of typing ("which bisph"), at least four letters in
 * and from the start of a word. The completion keeps the word the user began, a family's name or one of its aliases. */
function familySuggestions(value: string, families: ContaminantFamily[]): PaletteItem[] {
  if (value.startsWith("/")) return [];
  const lower = value.toLowerCase();
  const items: PaletteItem[] = [];
  for (const f of families) {
    const match = [f.term, ...f.aliases]
      .map((word) => {
        const w = word.toLowerCase();
        for (let n = Math.min(w.length - 1, lower.length); n >= 4; n--) {
          const start = lower.length - n;
          if ((start === 0 || /[\s(,;"']/.test(lower[start - 1])) && w.startsWith(lower.slice(start))) return { word, start };
        }
        return null;
      })
      .find((m) => m !== null);
    if (match) {
      items.push({
        label: f.name,
        detail: `${f.count} contaminants · ${f.description}`,
        insert: `${value.slice(0, match.start)}${match.word} `,
        complete: false,
        family: true,
      });
    }
  }
  return items;
}

/** Contaminant mode's family picker: a family inserts its name, which searches all its members; opening one lists
 * the members, and the filter matches families and member names alike. */
function FamiliesChip({ families, onPick }: { families: ContaminantFamily[]; onPick: (text: string, starter: string) => void }) {
  const [open, setOpen] = useState(false);
  const [filter, setFilter] = useState("");
  const [expanded, setExpanded] = useState<string | null>(null);
  const ref = useRef<HTMLDivElement>(null);
  const input = useRef<HTMLInputElement>(null);
  const shift = useOnScreen(ref, open, 384);
  // Focus without scrolling: autoFocus scrolled the page sideways before the menu had moved on screen.
  useEffect(() => {
    if (open) input.current?.focus({ preventScroll: true });
  }, [open]);
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
  const q = filter.trim().toLowerCase();
  const shown = families
    .map((f) => {
      const named = [f.name, f.term, ...f.aliases, f.description].some((text) => text.toLowerCase().includes(q));
      const members = q ? f.members.filter((m) => m.toLowerCase().includes(q)) : [];
      return { f, named, members };
    })
    .filter(({ named, members }) => !q || named || members.length > 0);
  const pick = (text: string, starter: string) => {
    setOpen(false);
    setFilter("");
    onPick(text, starter);
  };
  return (
    <div ref={ref} className="relative">
      <button
        type="button"
        onClick={() => setOpen(!open)}
        aria-expanded={open}
        title="Search contaminants by family"
        className="flex items-center gap-1 whitespace-nowrap rounded-full border border-brand/40 bg-brand-tint px-2.5 py-1 font-headline text-[13px] text-brand-ink transition-colors"
      >
        <span className="font-medium">Families</span>
        <span className="text-ink-2">{families.length}</span>
        <ChevronDown size={12} />
      </button>
      {open && (
        <div
          className="absolute bottom-full left-0 z-30 mb-2 w-[min(24rem,calc(100vw-2rem))] overflow-hidden rounded-xl border border-line bg-elevated shadow-float"
          style={{ left: shift }}
        >
          <div className="border-b border-line px-3 py-2">
            <p className="font-headline text-xs text-ink-2">
              Pick a family to search all its members, or open one to pick a single contaminant.
            </p>
            <input
              ref={input}
              type="search"
              value={filter}
              onChange={(e) => setFilter(e.target.value)}
              placeholder="Filter families or contaminants…"
              aria-label="Filter families or contaminants"
              className="mt-2 w-full rounded-lg border border-line bg-canvas px-2.5 py-1.5 font-headline text-sm text-ink outline-none placeholder:text-ink-2 focus:border-brand-soft"
            />
          </div>
          <ul className="max-h-80 overflow-y-auto py-1">
            {shown.map(({ f, members }) => {
              const listed = members.length ? members : expanded === f.name ? f.members : [];
              return (
                <li key={f.name}>
                  <div className="flex items-stretch hover:bg-muted">
                    <button type="button" onClick={() => pick(f.term, `Which ${f.term} leach from`)} className="min-w-0 flex-1 px-3 py-2 text-left">
                      <span className="flex items-baseline gap-2">
                        <span className="font-headline text-sm font-medium text-ink">{f.name}</span>
                        <span className="font-mono text-xs text-ink-2">{f.count}</span>
                        {f.source === "workbook" && <span className="rounded bg-muted px-1 font-headline text-[11px] text-ink-2">workbook</span>}
                      </span>
                      <span className="block font-headline text-xs text-ink-2">
                        {f.description} · e.g. {f.examples.join(", ")}
                      </span>
                    </button>
                    <button
                      type="button"
                      onClick={() => setExpanded(expanded === f.name ? null : f.name)}
                      aria-expanded={listed.length > 0}
                      aria-label={`Show the ${f.count} ${f.name.toLowerCase()}`}
                      className="px-2.5 text-ink-3 hover:text-ink"
                    >
                      <ChevronRight size={15} className={cx("transition-transform", listed.length > 0 && "rotate-90")} />
                    </button>
                  </div>
                  {listed.length > 0 && (
                    <ul className="mx-3 mb-1.5 max-h-48 overflow-y-auto border-l border-line pl-2">
                      {listed.map((m) => (
                        <li key={m}>
                          <button
                            type="button"
                            onClick={() => pick(m, `Does ${m} leach from`)}
                            className="w-full rounded px-1.5 py-1 text-left font-headline text-xs text-ink hover:bg-muted"
                          >
                            {m}
                          </button>
                        </li>
                      ))}
                    </ul>
                  )}
                </li>
              );
            })}
            {shown.length === 0 && (
              <li className="px-3 py-2 font-headline text-xs text-ink-2">
                Nothing matches “{filter.trim()}”. The agent also finds contaminants by name, CAS number or abbreviation.
              </li>
            )}
          </ul>
        </div>
      )}
    </div>
  );
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
  const shift = useOnScreen(ref, open, 320);
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
  const highlighted = value !== "off" && label !== "Assumptions" && label !== "Solvents" && label !== "Breadth";
  return (
    <div ref={ref} className="relative">
      <button
        type="button"
        onClick={() => setOpen(!open)}
        aria-expanded={open}
        title={command?.summary}
        className={cx(
          "flex items-center gap-1 whitespace-nowrap rounded-full border px-2.5 py-1 font-headline text-[13px] transition-colors",
          highlighted ? "border-brand/40 bg-brand-tint text-brand-ink" : "border-line bg-canvas text-ink hover:border-line-strong",
        )}
      >
        <span className={highlighted ? undefined : "text-ink-2"}>{command?.command ?? label}</span>
        <span className="font-medium">{value}</span>
        <ChevronDown size={12} />
      </button>
      {open && command && (
        <div
          className="absolute bottom-full left-0 z-30 mb-2 w-80 max-w-[calc(100vw-2rem)] overflow-hidden rounded-xl border border-line bg-elevated shadow-float"
          style={{ left: shift }}
        >
          <p className="border-b border-line px-3 py-2 font-headline text-xs text-ink-2">{command.summary}</p>
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
                  <Check size={14} className={cx("mt-0.5 shrink-0", option.value === value ? "text-brand-ink" : "invisible")} />
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
  families: ContaminantFamily[];
  state: SessionState | null;
  inputRef: RefObject<HTMLTextAreaElement | null>;
}) {
  const ref = props.inputRef;
  const [selected, setSelected] = useState(0);
  const [dismissed, setDismissed] = useState(false);
  const contaminantMode = (props.state?.contaminant ?? "off") !== "off";
  const items = useMemo(() => {
    if (dismissed) return [];
    const commands = palette(props.value, props.commands);
    return commands.length || !contaminantMode ? commands : familySuggestions(props.value, props.families);
  }, [dismissed, props.value, props.commands, props.families, contaminantMode]);

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
  // A picked family or contaminant goes where the cursor is; into an empty composer it starts a question.
  const insert = (text: string, starter: string) => {
    const el = ref.current;
    const value = props.value;
    const start = value.trim() ? (el?.selectionStart ?? value.length) : 0;
    const end = value.trim() ? (el?.selectionEnd ?? value.length) : value.length;
    const before = value.trim() ? value.slice(0, start) : "";
    const after = value.trim() ? value.slice(end) : "";
    const piece = value.trim() ? text : `${starter} `;
    const lead = before && !/\s$/.test(before) ? " " : "";
    const tail = after && !/^[\s,.?!;:]/.test(after) ? " " : "";
    props.onChange(`${before}${lead}${piece}${tail}${after}`);
    const caret = (before + lead + piece).length;
    requestAnimationFrame(() => {
      el?.focus();
      el?.setSelectionRange(caret, caret);
    });
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
      if (e.key === "Enter" && !e.shiftKey && !items[selected].family && items[selected].insert.trim() !== props.value.trim()) {
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
            {contaminantMode && props.families.length > 0 && <FamiliesChip families={props.families} onPick={insert} />}
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
                    <span className={cx("shrink-0 text-sm font-medium text-brand-ink", item.family ? "font-headline" : "font-mono")}>
                      {item.label}
                    </span>
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
            placeholder={
              contaminantMode ? "Name a contaminant or a family such as bisphenols…" : "Ask DISSOLVE… or type / for modes and commands"
            }
            aria-label="Message"
            className="block max-h-[220px] min-h-[52px] w-full resize-none rounded-xl border border-line bg-canvas py-3.5 pl-4 pr-14 font-headline text-[0.95rem] text-ink shadow-soft outline-none placeholder:text-ink-2 focus:border-brand-soft"
          />
          <button
            type="button"
            onClick={() => send(props.value)}
            disabled={!props.value.trim() || props.busy}
            aria-label={props.busy ? "Working" : "Send"}
            className="absolute bottom-2 right-2 flex h-9 w-9 items-center justify-center rounded-lg bg-brand text-on-brand transition-colors hover:bg-brand-hover disabled:cursor-not-allowed disabled:bg-muted disabled:text-ink-3"
          >
            {props.busy ? <Loader2 size={17} className="animate-spin" /> : <Send size={17} />}
          </button>
        </div>
        <p className="mt-1.5 hidden text-center font-headline text-xs text-ink-2 sm:block" aria-hidden>
          {items.some((item) => item.family) ? "Tab completes the family · Enter sends" : "Enter to send · Shift+Enter for a new line · / for commands"}
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
