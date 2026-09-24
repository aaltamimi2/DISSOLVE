import { useCallback, useEffect, useRef, useState } from "react";
import { api, type Command, type Doctor, type Features, type Model, type SessionRow, type SessionState, type StoredMessage, type TurnEvent } from "./api";
import { Composer, Header, MessageView, Sidebar, Toast, Welcome, type ChatMessage } from "./components";
import type { Example } from "./content";

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

export default function App() {
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
  const bottom = useRef<HTMLDivElement>(null);
  const composer = useRef<HTMLTextAreaElement>(null);
  const sessionRef = useRef<SessionState | null>(null);
  sessionRef.current = state;

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
        remember("dissolve-session", id);
        if (window.innerWidth < 1024) setSidebar(false);
      } catch (e) {
        remember("dissolve-session", null);
        notify((e as Error).message, "error");
      }
    },
    [notify],
  );

  useEffect(() => {
    api.health().then((h) => setFeatures(h.features)).catch(() => undefined);
    api.models().then(setModels).catch(() => undefined);
    api.commands().then(setCommands).catch((e: Error) => notify(`The DISSOLVE server is unreachable: ${e.message}`, "error"));
    refreshSessions();
    refreshDoctor();
    const last = remembered("dissolve-session");
    if (last) void openSession(last);
  }, [notify, openSession, refreshDoctor, refreshSessions]);

  const ensureSession = async (): Promise<string> => {
    if (sessionRef.current) return sessionRef.current.session_id;
    const created = await api.newSession(preferredModel || undefined);
    sessionRef.current = created;
    setState(created);
    remember("dissolve-session", created.session_id);
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

  const newChat = () => {
    setState(null);
    sessionRef.current = null;
    setMessages([]);
    setInput("");
    remember("dissolve-session", null);
  };

  const exportChat = () => {
    const blob = new Blob([toMarkdown(messages, state)], { type: "text/markdown" });
    const link = document.createElement("a");
    link.href = URL.createObjectURL(blob);
    link.download = `dissolve-${state?.session_id ?? "conversation"}.md`;
    link.click();
    URL.revokeObjectURL(link.href);
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
          doctor={doctor}
          onRefreshDoctor={() => refreshDoctor(true)}
        />
        <main className="flex min-w-0 flex-1 flex-col">
          <div className="min-h-0 flex-1 overflow-y-auto">
            {messages.length === 0 ? (
              <Welcome onPick={pick} features={features} />
            ) : (
              <div className="mx-auto w-full max-w-[860px] space-y-5 px-4 py-6">
                {messages.map((m) => (
                  <MessageView key={m.id} message={m} onCopy={copy} />
                ))}
                <div ref={bottom} />
              </div>
            )}
          </div>
          <Composer value={input} onChange={setInput} onSend={run} busy={busy} commands={commands} state={state ?? DEFAULTS} inputRef={composer} />
        </main>
      </div>
      {toast && <Toast text={toast.text} kind={toast.kind} />}
    </div>
  );
}
