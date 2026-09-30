import { useCallback, useEffect, useRef, useState } from "react";
import {
  api,
  SIGNED_OUT,
  type Command,
  type ContaminantFamily,
  type Doctor,
  type Features,
  type Model,
  type SessionRow,
  type SessionState,
  type StoredMessage,
  type TeaResult,
  type TurnEvent,
} from "./api";
import { Composer, Header, MessageView, Sidebar, SignIn, Toast, Welcome, type ChatMessage } from "./components";
import type { Example } from "./content";
import { TeaPanel, type TeaView } from "./tea";

const uid = () => Math.random().toString(36).slice(2);

function remembered(key: string): string | null {
  try {
    return localStorage.getItem(key);
  } catch {
    return null;
  }
}

function remember(key: string, value: string | null) {
  try {
    if (value === null) localStorage.removeItem(key);
    else localStorage.setItem(key, value);
  } catch {
    /* private mode: preferences simply do not persist */
  }
}

/** The modes a new session starts with, shown before the first message creates it. */
const DEFAULTS: SessionState = {
  session_id: "",
  model: "",
  model_label: "",
  model_ready: true,
  mode: "review",
  contaminant: "off",
  literature: "off",
  solvents: "common",
  breadth: "1",
};

function fromStored(messages: StoredMessage[]): ChatMessage[] {
  return messages.map((m) => (m.role === "user" ? { id: uid(), role: "user", text: m.text } : { id: uid(), role: "assistant", text: m.text, tools: m.tools, status: m.status }));
}

function toMarkdown(messages: ChatMessage[], state: SessionState | null): string {
  const lines = [`# DISSOLVE conversation${state ? ` ${state.session_id}` : ""}`, "", `Exported ${new Date().toLocaleString()}.`, ""];
  for (const m of messages) {
    if (m.role === "user") lines.push(`## You`, "", m.text, "");
    else if (m.role === "assistant") {
      lines.push(`## DISSOLVE`, "");
      if (m.tools.length) lines.push(`<details><summary>${m.tools.length} tool calls</summary>`, "", ...m.tools.map((t) => `- \`${t.summary}\``), "", "</details>", "");
      lines.push(m.text, "");
    } else if (m.role === "command") lines.push("```", m.command, m.text, "```", "");
    else lines.push(`> Error: ${m.text}`, "");
  }
  return lines.join("\n");
}

/** A server with accounts shows the sign-in page until someone signs in; a local one opens straight away. */
export default function App() {
  const [auth, setAuth] = useState<{ accounts: boolean; accessCode: boolean; adminReads: boolean; user: string | null } | null>(null);
  useEffect(() => {
    let live = true;
    const load = async () => {
      try {
        const config = await api.authConfig();
        const me = config.accounts ? await api.me().catch(() => null) : null;
        if (live) setAuth({ accounts: config.accounts, accessCode: config.access_code, adminReads: Boolean(config.admin_reads), user: me?.username ?? null });
      } catch {
        if (live) setAuth({ accounts: false, accessCode: false, adminReads: false, user: null });
      }
    };
    void load();
    const signedOut = () => setAuth((current) => (current?.accounts ? { ...current, user: null } : current));
    window.addEventListener(SIGNED_OUT, signedOut);
    return () => {
      live = false;
      window.removeEventListener(SIGNED_OUT, signedOut);
    };
  }, []);
  if (!auth) return <div className="h-dvh bg-canvas" />;
  if (auth.accounts && !auth.user) {
    return <SignIn accessCode={auth.accessCode} adminReads={auth.adminReads} onSignedIn={(user) => setAuth({ ...auth, user })} />;
  }
  const signOut = async () => {
    await api.logout().catch(() => undefined);
    setAuth({ ...auth, user: null });
  };
  return <Workspace key={auth.user ?? "local"} user={auth.user} onSignOut={auth.accounts ? signOut : undefined} />;
}

function Workspace({ user, onSignOut }: { user: string | null; onSignOut?: () => void }) {
  const lastSession = user ? `dissolve-session:${user.toLowerCase()}` : "dissolve-session";
  const [theme, setTheme] = useState<"light" | "dark">(() => (remembered("dissolve-theme") === "dark" ? "dark" : "light"));
  const [sidebar, setSidebar] = useState(() => (remembered("dissolve-sidebar") ?? (window.innerWidth >= 1024 ? "open" : "closed")) === "open");
  const [tab, setTab] = useState<"conversations" | "system">("conversations");
  const [doctor, setDoctor] = useState<Doctor | null>(null);
  const [models, setModels] = useState<Model[]>([]);
  const [commands, setCommands] = useState<Command[]>([]);
  const [sessions, setSessions] = useState<SessionRow[]>([]);
  const [state, setState] = useState<SessionState | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [toast, setToast] = useState<{ text: string; kind: "info" | "error" } | null>(null);
  const [preferredModel, setPreferredModel] = useState(() => remembered("dissolve-model") ?? "");
  const [features, setFeatures] = useState<Features>({ literature: true, tea: true });
  const [families, setFamilies] = useState<ContaminantFamily[]>([]);
  const [tea, setTea] = useState<TeaView | null>(null);
  const [teaOpen, setTeaOpen] = useState(false);
  const bottom = useRef<HTMLDivElement>(null);
  const composer = useRef<HTMLTextAreaElement>(null);
  const sessionRef = useRef<SessionState | null>(null);
  sessionRef.current = state;
  const teaOffered = useRef(true);
  teaOffered.current = features.tea;

  const notify = useCallback((text: string, kind: "info" | "error" = "info") => {
    setToast({ text, kind });
    window.setTimeout(() => setToast(null), 3000);
  }, []);
  const refreshSessions = useCallback(() => api.sessions().then(setSessions).catch(() => undefined), []);
  const refreshDoctor = useCallback((refresh = false) => {
    setDoctor(null);
    api.doctor(refresh).then(setDoctor).catch((e: Error) => notify(`dissolve doctor failed: ${e.message}`, "error"));
  }, [notify]);

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    remember("dissolve-theme", theme);
  }, [theme]);
  // Effects return nothing: React calls a returned value as the cleanup, and newer Chrome's scrollIntoView returns a Promise.
  useEffect(() => {
    remember("dissolve-sidebar", sidebar ? "open" : "closed");
  }, [sidebar]);
  useEffect(() => {
    bottom.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [messages]);

  const openSession = useCallback(
    async (id: string) => {
      try {
        const loaded = await api.session(id);
        const { messages: stored, ...current } = loaded;
        setState(current);
        setMessages(fromStored(stored));
        remember(lastSession, id);
        setTea(null);
        setTeaOpen(false);
        // A sheet still waiting for an answer comes back after a reload; the last results hang on the last TEA answer.
        if (teaOffered.current) {
          api
            .teaState(id)
            .then((panel) => {
              if (panel.sheet) {
                setTea({ phase: "sheet", sheet: panel.sheet, progress: null, result: null, followed: false });
                setTeaOpen(true);
              } else if (panel.progress) setTea({ phase: "running", sheet: null, progress: panel.progress, result: null, followed: false });
              const result = panel.result;
              if (result) {
                setMessages((all) => {
                  const last = all.map((m) => m.role === "assistant" && m.tools.some((t) => t.name === "evaluate_process")).lastIndexOf(true);
                  return all.map((m, i) => (i === last && m.role === "assistant" ? { ...m, teaResult: result } : m));
                });
              }
            })
            .catch(() => undefined);
        }
        if (window.innerWidth < 1024) setSidebar(false);
      } catch (e) {
        remember(lastSession, null);
        notify((e as Error).message, "error");
      }
    },
    [notify, lastSession],
  );

  useEffect(() => {
    api.health().then((h) => setFeatures(h.features)).catch(() => undefined);
    api.models().then(setModels).catch(() => undefined);
    api.commands().then(setCommands).catch((e: Error) => notify(`The DISSOLVE server is unreachable: ${e.message}`, "error"));
    api.contaminantFamilies().then(setFamilies).catch(() => undefined);
    refreshSessions();
    refreshDoctor();
    const last = remembered(lastSession);
    if (last) void openSession(last);
  }, [notify, openSession, refreshDoctor, refreshSessions, lastSession]);

  const ensureSession = async (): Promise<string> => {
    if (sessionRef.current) return sessionRef.current.session_id;
    const created = await api.newSession(preferredModel || undefined);
    sessionRef.current = created;
    setState(created);
    remember(lastSession, created.session_id);
    return created.session_id;
  };

  const run = async (text: string) => {
    if (busy) return;
    setBusy(true);
    const command = text.startsWith("/");
    const replyId = uid();
    if (!command) {
      setMessages((m) => [...m, { id: uid(), role: "user", text }, { id: replyId, role: "assistant", text: "", tools: [], running: true }]);
    }
    const update = (patch: (m: Extract<ChatMessage, { role: "assistant" }>) => ChatMessage) =>
      setMessages((all) => all.map((m) => (m.id === replyId && m.role === "assistant" ? patch(m) : m)));
    const onEvent = (event: TurnEvent) => {
      if (event.event === "tool") update((m) => ({ ...m, tools: [...m.tools, event] }));
      else if (event.event === "command.output") {
        setState(event.state);
        setMessages((m) => [...m, { id: uid(), role: "command", command: text, text: event.text }]);
      } else if (event.event === "tea.sheet") {
        setTea({ phase: "sheet", sheet: event.sheet, progress: null, result: null, followed: true });
        setTeaOpen(true);
        if (window.innerWidth < 1440) setSidebar(false); // the chat keeps room beside the panel
        update((m) => ({ ...m, tea: "waiting" }));
      } else if (event.event === "tea.progress") {
        setTea((t) =>
          t && {
            ...t,
            phase: "running",
            progress: {
              sheet_id: event.sheet_id,
              done: event.done,
              total: event.total,
              running: event.running ?? null,
              rows: [...(t.progress?.sheet_id === event.sheet_id ? t.progress.rows : []), ...(event.rows ?? [])],
            },
          },
        );
        update((m) => ({ ...m, tea: "running", teaProgress: `${event.done} of ${event.total} plants done` }));
      } else if (event.event === "tea.result") {
        const result: TeaResult = { ...event };
        setTea((t) => ({ phase: "done", sheet: t?.sheet ?? null, progress: null, result, followed: true }));
        update((m) => ({ ...m, tea: undefined, teaProgress: undefined, teaResult: result }));
      } else if (event.event === "tea.closed") {
        setTea((t) => (event.reason === "error" || (t?.phase === "sheet" && t.sheet?.id === event.sheet_id) ? null : t));
        setTeaOpen(false);
        if (event.reason === "error") notify(`The TEA run stopped: ${event.message ?? "an error"}`, "error");
        update((m) => ({ ...m, tea: undefined }));
      } else if (event.event === "turn.completed") {
        setState(event.state);
        update((m) => ({ ...m, text: event.answer, status: event.status, elapsed: event.elapsed_s, running: false }));
      } else if (event.event === "error") {
        setMessages((all) => [...all.filter((m) => m.id !== replyId || (m.role === "assistant" && m.tools.length > 0)), { id: uid(), role: "error", text: event.message }]);
        update((m) => ({ ...m, running: false, text: m.text || "_The turn stopped before an answer._" }));
      }
    };
    try {
      const id = await ensureSession();
      await api.turn(id, text, onEvent);
    } catch (e) {
      onEvent({ event: "error", message: (e as Error).message });
    } finally {
      update((m) => (m.running ? { ...m, running: false } : m));
      setBusy(false);
      refreshSessions();
    }
  };

  const pick = async (example: Example) => {
    setInput(example.text);
    composer.current?.focus();
    if (!example.needs) return;
    const [name, value] = example.needs.split(" ");
    const current = sessionRef.current ?? DEFAULTS;
    const key = name.slice(1) as keyof SessionState;
    if (String(current[key]) !== value) await run(example.needs);
  };

  const chooseModel = (alias: string) => {
    setPreferredModel(alias);
    remember("dissolve-model", alias);
    if (sessionRef.current) void run(`/model ${alias}`);
  };

  const openTea = (result?: TeaResult) => {
    if (!(tea && (tea.phase === "sheet" || tea.phase === "running")) && result) {
      setTea({ phase: "done", sheet: null, progress: null, result, followed: true });
    }
    setTeaOpen(true);
  };

  const newChat = () => {
    setState(null);
    sessionRef.current = null;
    setMessages([]);
    setTea(null);
    setTeaOpen(false);
    setInput("");
    remember(lastSession, null);
  };

  const deleteSession = async (id: string) => {
    try {
      await api.deleteSession(id);
      if (sessionRef.current?.session_id === id) newChat();
      notify("Chat deleted");
    } catch (e) {
      notify((e as Error).message, "error");
    } finally {
      refreshSessions();
    }
  };

  const exportChat = () => {
    const blob = new Blob([toMarkdown(messages, state)], { type: "text/markdown" });
    const link = document.createElement("a");
    link.href = URL.createObjectURL(blob);
    link.download = `dissolve-${state?.session_id ?? "conversation"}.md`;
    link.click();
    URL.revokeObjectURL(link.href);
  };

  /** Send a report about the answer at `index`, with the question it answered; true when it was saved. */
  const report = async (index: number, note: string): Promise<boolean> => {
    const id = sessionRef.current?.session_id;
    const question = [...messages.slice(0, index)].reverse().find((m) => m.role === "user");
    if (!id || !question || question.role !== "user") return false;
    try {
      await api.report(id, question.text, note);
      notify("Report sent. Thank you.");
      return true;
    } catch (e) {
      notify(`The report did not go through: ${(e as Error).message}`, "error");
      return false;
    }
  };

  const copy = (text: string) =>
    navigator.clipboard.writeText(text).then(
      () => notify("Copied"),
      () => notify("Copying was blocked by the browser", "error"),
    );

  const defaultModel = models.find((m) => m.default)?.alias ?? "";
  return (
    <div className="flex h-dvh flex-col bg-canvas text-ink">
      <Header
        doctor={doctor}
        models={models}
        model={state?.model || preferredModel || defaultModel}
        theme={theme}
        canExport={messages.length > 0}
        onToggleSidebar={() => setSidebar(!sidebar)}
        onShowSystem={() => {
          setTab("system");
          setSidebar(true);
        }}
        onModel={chooseModel}
        onTheme={() => setTheme(theme === "light" ? "dark" : "light")}
        onExport={exportChat}
        onNewChat={newChat}
        user={user}
        onSignOut={onSignOut}
      />
      <div className="flex min-h-0 flex-1">
        <Sidebar
          open={sidebar}
          tab={tab}
          onTab={setTab}
          onClose={() => setSidebar(false)}
          sessions={sessions}
          current={state?.session_id ?? null}
          onOpenSession={openSession}
          onDeleteSession={deleteSession}
          doctor={doctor}
          onRefreshDoctor={() => refreshDoctor(true)}
        />
        <main className="flex min-w-0 flex-1 flex-col">
          <div className="min-h-0 flex-1 overflow-y-auto">
            {messages.length === 0 ? (
              <Welcome onPick={pick} features={features} />
            ) : (
              <div className="mx-auto w-full max-w-[860px] space-y-5 px-4 py-6">
                {messages.map((m, index) => (
                  <MessageView
                    key={m.id}
                    message={m}
                    onCopy={copy}
                    onOpenTea={openTea}
                    onReport={m.role === "assistant" && !m.running && state ? (note) => report(index, note) : undefined}
                  />
                ))}
                <div ref={bottom} />
              </div>
            )}
          </div>
          <Composer
            value={input}
            onChange={setInput}
            onSend={run}
            busy={busy}
            commands={commands}
            families={families}
            state={state ?? DEFAULTS}
            inputRef={composer}
            onTeaSheet={features.tea ? () => void run("/process") : undefined}
          />
        </main>
        {tea && teaOpen && state && (
          <TeaPanel sessionId={state.session_id} view={tea} onView={setTea} onClose={() => setTeaOpen(false)} notify={notify} />
        )}
      </div>
      {toast && <Toast text={toast.text} kind={toast.kind} />}
    </div>
  );
}
