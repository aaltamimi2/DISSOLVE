// The TEA panel: when the model starts a plant TEA, every field of the CLI's process sheet, prefilled with the model's
// values and the first-run defaults. The person keeps them or edits them, turns any number into a range (a full grid
// across ranged fields), and runs; the server checks every plant before anything runs (src/dissolve/web_tea.py).
import { BarChart3, Calculator, ChevronDown, Download, Loader2, Play, RotateCcw, SlidersHorizontal, Square, X } from "lucide-react";
import { useEffect, useId, useMemo, useState, type ReactNode } from "react";
import { api, ApiError, type TeaCheck, type TeaField, type TeaProgress, type TeaRange, type TeaResult, type TeaRow, type TeaSheet, type TeaValue } from "./api";

const cx = (...parts: (string | false | null | undefined)[]) => parts.filter(Boolean).join(" ");

/** What the panel shows: a sheet waiting for the person, a run, or its results. `followed` is false when this page
 * did not start the turn (a reload), so no progress will stream to it. */
export type TeaView = {
  phase: "sheet" | "running" | "done";
  sheet: TeaSheet | null;
  progress: TeaProgress | null;
  result: TeaResult | null;
  followed: boolean;
};

const ORIGIN: Record<string, { label: string; title: string; tone: string }> = {
  model: { label: "model", title: "The model proposed this value", tone: "bg-brand-tint text-brand-ink" },
  default: { label: "default", title: "The first-run default the CLI's process sheet uses", tone: "bg-muted text-ink-2" },
  you: { label: "you", title: "You changed this value", tone: "bg-navy text-white" },
  previous: { label: "your last run", title: "Your value from an earlier run in this chat", tone: "bg-navy/15 text-ink" },
  reference: { label: "reference plant", title: "The stored reference plant (LDPE in dodecane, C1) the TEA sheet button opens with", tone: "bg-muted text-ink-2" },
  admitted: { label: "solvent table", title: "The admitted price of this solvent", tone: "bg-muted text-ink-2" },
  screen: { label: "screen", title: "From the solvent screen", tone: "bg-brand-tint text-brand-ink" },
  inherited: { label: "earlier result", title: "From an earlier result in this chat", tone: "bg-brand-tint text-brand-ink" },
  missing: { label: "needed", title: "The engine needs a value here", tone: "bg-bad/10 text-bad" },
};

const METRICS = [
  { key: "msp_usd_per_kg", label: "MSP", unit: "USD/kg" },
  { key: "gwp_kg_co2e_per_kg", label: "GWP", unit: "kg CO₂e/kg" },
  { key: "tci_usd", label: "TCI", unit: "USD" },
  { key: "aoc_usd_per_yr", label: "AOC", unit: "USD/yr" },
  { key: "total_energy_mj_per_kg", label: "Energy", unit: "MJ/kg" },
] as const;
type MetricKey = (typeof METRICS)[number]["key"];
const SERIES = ["var(--chart-1)", "var(--chart-2)", "var(--chart-3)", "var(--chart-4)", "var(--chart-5)", "var(--chart-6)"];

/** Three significant figures, the rounding answers use; millions and up in compact form (12.3M). `pad` keeps the
 * trailing zeros a metric's figures carry (1.10, not 1.1), so the panel reads as the answer does. */
function fmt(value: number | null | undefined, digits = 3, pad = false): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return "–";
  const compact = Math.abs(value) >= 1e6;
  return new Intl.NumberFormat("en-US", {
    maximumSignificantDigits: digits,
    minimumSignificantDigits: pad ? digits : undefined,
    notation: compact ? "compact" : "standard",
  }).format(value);
}

const clean = (value: number) => Number(value.toPrecision(12));

/** A field's value as a table cell: percent fields in percent, booleans as yes or no; the unit is in the header. */
function plain(value: TeaValue | undefined, percent = false): string {
  if (value === null || value === undefined) return "–";
  if (typeof value === "boolean") return value ? "yes" : "no";
  if (Array.isArray(value)) return value.map((item) => (percent ? fmt(item * 100, 4) : String(item))).join(percent ? " / " : "–");
  if (typeof value === "number") return percent ? fmt(value * 100, 6) : fmt(value, 6);
  return value;
}

const same = (a: unknown, b: unknown) => JSON.stringify(a) === JSON.stringify(b);

function duration(seconds: number): string {
  if (seconds < 60) return `${Math.max(1, Math.round(seconds))} s`;
  if (seconds < 3600) return `${Math.round(seconds / 60)} min`;
  return `${(seconds / 3600).toFixed(1)} h`;
}

/** A range as typed: numbers stay text until they parse, so "0." or "1e" can be on the way to a value. */
type Draft =
  | { kind: "linear"; from: string; to: string; step: string }
  | { kind: "log"; from: string; to: string; points: string }
  | { kind: "list"; text: string }
  | { kind: "pick"; picked: (string | boolean)[] };

const shown = (field: TeaField, value: number) => String(clean(field.percent ? value * 100 : value));

function toDraft(field: TeaField, range: TeaRange | undefined, value: TeaValue): Draft {
  if (field.kind === "bool" || field.kind === "choice") {
    const picked = range?.kind === "list" ? range.values.filter((v) => typeof v !== "number") : [];
    return { kind: "pick", picked: picked.length ? (picked as (string | boolean)[]) : value === null || Array.isArray(value) ? [] : [value as string | boolean] };
  }
  if (range?.kind === "linear") return { kind: "linear", from: shown(field, range.from), to: shown(field, range.to), step: shown(field, range.step) };
  if (range?.kind === "log") return { kind: "log", from: shown(field, range.from), to: shown(field, range.to), points: String(range.points) };
  if (range?.kind === "list") return { kind: "list", text: range.values.map((v) => (typeof v === "number" ? shown(field, v) : String(v))).join(", ") };
  if (typeof value === "number" && value !== 0) {
    const round = (v: number) => Number(v.toPrecision(2));
    return { kind: "linear", from: shown(field, round(value * 0.5)), to: shown(field, round(value * 1.5)), step: shown(field, round(value * 0.25)) };
  }
  return { kind: "linear", from: "", to: "", step: "" };
}

const number = (text: string) => {
  const token = text.trim().replaceAll(",", "");
  return token === "" ? Number.NaN : Number(token);
};

/** A draft as the engine's range (fractions for percent fields), with how many values it makes, or why it cannot. */
function fromDraft(field: TeaField, draft: Draft): { range: TeaRange; count: number } | { error: string } {
  const engine = (v: number) => clean(field.percent ? v / 100 : v);
  if (draft.kind === "pick") {
    if (!draft.picked.length) return { error: `${field.label}: pick at least one value` };
    return { range: { kind: "list", values: draft.picked }, count: draft.picked.length };
  }
  if (draft.kind === "list") {
    const parts = draft.text.split(/[\s;,]+/).filter(Boolean);
    const values = parts.map(number);
    if (!parts.length) return { error: `${field.label}: list at least one value` };
    if (values.some((v) => !Number.isFinite(v))) return { error: `${field.label}: every value must be a number` };
    return { range: { kind: "list", values: values.map(engine) }, count: new Set(values).size };
  }
  const from = number(draft.from);
  const to = number(draft.to);
  if (!Number.isFinite(from) || !Number.isFinite(to)) return { error: `${field.label}: give the range a start and an end` };
  if (to < from) return { error: `${field.label}: the end is below the start` };
  if (draft.kind === "log") {
    const points = Number(draft.points);
    if (!Number.isInteger(points) || points < 2) return { error: `${field.label}: log spacing takes 2 or more points` };
    if (from <= 0) return { error: `${field.label}: log spacing needs a start above zero` };
    return { range: { kind: "log", from: engine(from), to: engine(to), points }, count: points };
  }
  const step = number(draft.step);
  if (!Number.isFinite(step) || step <= 0) return { error: `${field.label}: the step must be above zero` };
  return { range: { kind: "linear", from: engine(from), to: engine(to), step: engine(step) }, count: Math.floor((to - from) / step + 1e-9) + 1 };
}

function Badge({ origin }: { origin: string }) {
  const meta = ORIGIN[origin] ?? ORIGIN.model;
  return (
    <span title={meta.title} className={cx("shrink-0 rounded px-1.5 py-px font-headline text-[10.5px] font-medium", meta.tone)}>
      {meta.label}
    </span>
  );
}

const inputClass = "w-full rounded-md border border-line bg-canvas px-2 py-1 font-headline text-sm text-ink outline-none focus:border-brand-soft disabled:opacity-60";

/** A number typed in the field's display unit (percent for fractions); `onChange` gets the engine's value, or `ok`
 * false while the text is not a number yet. */
function NumberInput({ id, value, percent, onChange, disabled, invalid, label }: {
  id?: string;
  value: number | null;
  percent: boolean;
  onChange: (value: number | null, ok: boolean) => void;
  disabled?: boolean;
  invalid?: boolean;
  label?: string;
}) {
  const display = value === null || value === undefined ? "" : String(clean(percent ? value * 100 : value));
  const [text, setText] = useState(display);
  const [focused, setFocused] = useState(false);
  useEffect(() => {
    if (!focused) setText(display);
  }, [display, focused]);
  return (
    <input
      id={id}
      aria-label={label}
      inputMode="decimal"
      disabled={disabled}
      value={focused ? text : display}
      onFocus={() => {
        setText(display);
        setFocused(true);
      }}
      onBlur={() => setFocused(false)}
      onChange={(e) => {
        setText(e.target.value);
        const token = e.target.value.trim();
        if (!token) return onChange(null, true);
        const parsed = number(token);
        if (Number.isFinite(parsed)) onChange(clean(percent ? parsed / 100 : parsed), true);
        else onChange(value, false);
      }}
      className={cx(inputClass, "text-right tabular-nums", invalid && "border-bad")}
    />
  );
}

function Segmented<T extends string>({ value, options, onChange }: { value: T; options: [T, string][]; onChange: (value: T) => void }) {
  return (
    <span className="inline-flex rounded-md bg-muted p-0.5">
      {options.map(([key, label]) => (
        <button
          key={key}
          type="button"
          onClick={() => onChange(key)}
          aria-pressed={value === key}
          className={cx("rounded px-2 py-0.5 font-headline text-xs", value === key ? "bg-canvas font-medium text-ink shadow-soft" : "text-ink-2 hover:text-ink")}
        >
          {label}
        </button>
      ))}
    </span>
  );
}

function RangeEditor({ field, draft, onChange, count, error }: { field: TeaField; draft: Draft; onChange: (draft: Draft) => void; count: number | null; error?: string }) {
  const unit = field.unit ? ` ${field.unit}` : "";
  const box = (label: string, value: string, set: (text: string) => void) => (
    <label className="min-w-0 flex-1">
      <span className="block font-headline text-[11px] text-ink-2">{label}</span>
      <input inputMode="decimal" value={value} onChange={(e) => set(e.target.value)} className={cx(inputClass, "text-right tabular-nums")} />
    </label>
  );
  return (
    <div className="mt-2 rounded-lg border border-brand/30 bg-brand-tint/40 p-2.5">
      {draft.kind === "pick" ? (
        <div className="flex flex-wrap gap-x-4 gap-y-1.5">
          {(field.kind === "bool" ? [{ value: true, label: "yes" }, { value: false, label: "no" }] : (field.options ?? []).map((o) => ({ value: o.value, label: o.value }))).map((option) => {
            const on = draft.picked.includes(option.value);
            return (
              <label key={String(option.value)} className="flex items-center gap-1.5 font-headline text-sm text-ink">
                <input
                  type="checkbox"
                  checked={on}
                  onChange={() => onChange({ kind: "pick", picked: on ? draft.picked.filter((v) => v !== option.value) : [...draft.picked, option.value] })}
                  className="accent-[var(--primary-ink)]"
                />
                {option.label}
              </label>
            );
          })}
        </div>
      ) : (
        <>
          <div className="mb-2 flex items-center justify-between gap-2">
            <Segmented
              value={draft.kind}
              options={[["linear", "Steps"], ["log", "Log"], ["list", "List"]]}
              onChange={(kind) =>
                onChange(
                  kind === "list"
                    ? { kind, text: draft.kind === "list" ? draft.text : [draft.from, draft.to].filter(Boolean).join(", ") }
                    : kind === "log"
                      ? { kind, from: "from" in draft ? draft.from : "", to: "to" in draft ? draft.to : "", points: "5" }
                      : { kind, from: "from" in draft ? draft.from : "", to: "to" in draft ? draft.to : "", step: "" },
                )
              }
            />
            <span className="font-headline text-xs text-ink-2">{count !== null ? `${count.toLocaleString("en-US")} value${count === 1 ? "" : "s"}` : ""}</span>
          </div>
          {draft.kind === "list" ? (
            <label className="block">
              <span className="block font-headline text-[11px] text-ink-2">Values{unit}, separated by commas</span>
              <input value={draft.text} onChange={(e) => onChange({ kind: "list", text: e.target.value })} className={inputClass} />
            </label>
          ) : (
            <div className="flex gap-2">
              {box(`From${unit}`, draft.from, (from) => onChange({ ...draft, from }))}
              {box(`To${unit}`, draft.to, (to) => onChange({ ...draft, to }))}
              {draft.kind === "linear"
                ? box(`Step${unit}`, draft.step, (step) => onChange({ ...draft, step }))
                : box("Points", draft.points, (points) => onChange({ ...draft, points }))}
            </div>
          )}
        </>
      )}
      {error && <p className="mt-1.5 font-headline text-xs text-bad">{error}</p>}
    </div>
  );
}

function FieldRow(props: {
  field: TeaField;
  value: TeaValue;
  origin: string;
  fallback: TeaValue | undefined;
  hasDefault: boolean;
  draft: Draft | undefined;
  rangeCount: number | null;
  rangeError?: string;
  paired: boolean;
  invalid: boolean;
  suggestions?: string[];
  note?: ReactNode;
  onValue: (value: TeaValue, ok?: boolean) => void;
  onDraft: (draft: Draft | undefined) => void;
}) {
  const { field, value, draft } = props;
  const id = useId();
  const locked = !field.editable || props.paired;
  const resettable = props.hasDefault && !draft && !props.paired && field.editable && !same(value, props.fallback);
  let input: ReactNode;
  if (props.paired) input = <span className="font-headline text-sm text-ink-2">Set per plant below</span>;
  else if (draft) input = <span className="font-headline text-sm text-brand-ink">Ranged below</span>;
  else if (field.kind === "number")
    input = <NumberInput id={id} value={value as number | null} percent={field.percent} disabled={locked} invalid={props.invalid} onChange={(v, ok) => props.onValue(v, ok)} />;
  else if (field.kind === "bool")
    input = (
      <label className="flex items-center gap-2 font-headline text-sm text-ink">
        <input id={id} type="checkbox" disabled={locked} checked={value === true} onChange={(e) => props.onValue(e.target.checked)} className="h-4 w-4 accent-[var(--primary-ink)]" />
        {value === true ? "yes" : "no"}
      </label>
    );
  else if (field.kind === "choice")
    input = (
      <select id={id} disabled={locked} value={String(value ?? "")} onChange={(e) => props.onValue(e.target.value)} className={inputClass}>
        {(field.options ?? []).map((option) => (
          <option key={option.value} value={option.value}>
            {option.label}
          </option>
        ))}
      </select>
    );
  else if (field.kind === "pair") {
    const pair = Array.isArray(value) ? value : [null, null];
    input = (
      <span className="flex items-center gap-1.5">
        <NumberInput label={`${field.label}: first year`} value={pair[0] ?? null} percent={false} disabled={locked} onChange={(v, ok) => props.onValue([v ?? Number.NaN, pair[1] ?? Number.NaN], ok && v !== null)} />
        <span className="text-ink-3">–</span>
        <NumberInput label={`${field.label}: last year`} value={pair[1] ?? null} percent={false} disabled={locked} onChange={(v, ok) => props.onValue([pair[0] ?? Number.NaN, v ?? Number.NaN], ok && v !== null)} />
      </span>
    );
  } else if (field.kind === "list") {
    const shares = Array.isArray(value) ? value : [];
    const total = shares.reduce((sum, item) => sum + (Number.isFinite(item) ? item : 0), 0);
    input = (
      <span className="block">
        <span className="flex flex-wrap items-center gap-1.5">
          {shares.map((share, index) => (
            <span key={index} className="w-16">
              <NumberInput
                label={`${field.label}: year ${index + 1}`}
                value={share}
                percent
                disabled={locked}
                onChange={(v, ok) => props.onValue(shares.map((item, i) => (i === index ? (v ?? 0) : item)), ok)}
              />
            </span>
          ))}
          {!locked && (
            <>
              <button type="button" onClick={() => props.onValue([...shares, 0])} className="rounded-md bg-muted px-1.5 font-headline text-xs text-ink hover:bg-line">
                + year
              </button>
              {shares.length > 1 && (
                <button type="button" onClick={() => props.onValue(shares.slice(0, -1))} className="rounded-md bg-muted px-1.5 font-headline text-xs text-ink hover:bg-line">
                  − year
                </button>
              )}
            </>
          )}
        </span>
        <span className={cx("mt-0.5 block font-headline text-[11px]", Math.abs(total - 1) > 1e-6 ? "text-bad" : "text-ink-2")}>Sum {fmt(total * 100, 4)} %</span>
      </span>
    );
  } else if (props.suggestions) {
    // Every polymer or solvent live TEA takes, always listed: a datalist showed only the entries matching what the
    // field already held, so a sheet opened on LDPE in dodecane offered nothing else (owner, 2026-09-30).
    const current = String(value ?? "");
    const known = props.suggestions.find((name) => name.toLowerCase() === current.toLowerCase());
    input = (
      <select id={id} disabled={locked} value={known ?? current} onChange={(e) => props.onValue(e.target.value)} className={inputClass}>
        {!known && <option value={current}>{current || "Choose…"}</option>}
        {props.suggestions.map((name) => (
          <option key={name} value={name}>
            {name.replace(/_/g, " ")}
          </option>
        ))}
      </select>
    );
  } else {
    input = <input id={id} disabled={locked} value={String(value ?? "")} onChange={(e) => props.onValue(e.target.value)} className={inputClass} spellCheck={false} />;
  }
  const wide = field.kind === "list" || field.kind === "pair" || field.kind === "choice";
  return (
    <div className="border-t border-line/70 py-2 first:border-t-0">
      <div className={cx("grid items-center gap-x-2 gap-y-1", wide ? "grid-cols-1" : "grid-cols-[minmax(0,1fr)_9.5rem]")}>
        <div className="flex min-w-0 items-center gap-1.5">
          <label htmlFor={id} className="min-w-0 font-headline text-[13px] leading-snug text-ink" title={field.name}>
            {field.label}
            {field.unit && !field.percent && field.kind === "number" ? <span className="text-ink-2"> ({field.unit})</span> : null}
            {field.percent ? <span className="text-ink-2"> (%)</span> : null}
          </label>
          <Badge origin={props.origin} />
        </div>
        <div className="flex min-w-0 items-center gap-1">
          <div className="min-w-0 flex-1">{input}</div>
          {field.rangeable && !props.paired && (
            <button
              type="button"
              onClick={() => props.onDraft(draft ? undefined : toDraft(field, undefined, value))}
              title={draft ? "Use one value" : "Run a range of values"}
              aria-label={draft ? `${field.label}: use one value` : `${field.label}: run a range of values`}
              aria-pressed={Boolean(draft)}
              className={cx("shrink-0 rounded-md p-1", draft ? "bg-brand text-on-brand" : "text-ink-2 hover:bg-muted hover:text-ink")}
            >
              <SlidersHorizontal size={14} />
            </button>
          )}
          {resettable && (
            <button
              type="button"
              onClick={() => props.onValue(props.fallback ?? null)}
              title="Reset to the default"
              aria-label={`${field.label}: reset to the default`}
              className="shrink-0 rounded-md p-1 text-ink-2 hover:bg-muted hover:text-ink"
            >
              <RotateCcw size={13} />
            </button>
          )}
        </div>
      </div>
      {field.hint && <p className="mt-0.5 font-headline text-[11px] text-ink-2">{field.hint}</p>}
      {props.note}
      {draft && <RangeEditor field={field} draft={draft} onChange={(next) => props.onDraft(next)} count={props.rangeCount} error={props.rangeError} />}
    </div>
  );
}

const NO_CHECK: TeaCheck = { plants: 0, stored: 0, live: 0, seconds: 0, invalid: [], invalid_count: 0, confirm: false, runnable: false };

function SheetEditor({ sessionId, sheet, onStarted, onGone, notify }: {
  sessionId: string;
  sheet: TeaSheet;
  onStarted: (plants: number) => void;
  onGone: () => void;
  notify: (text: string, kind?: "info" | "error") => void;
}) {
  const fields = useMemo(() => new Map(sheet.fields.map((field) => [field.name, field])), [sheet]);
  const [values, setValues] = useState<Record<string, TeaValue>>(sheet.values);
  const [drafts, setDrafts] = useState<Record<string, Draft>>(() =>
    Object.fromEntries(Object.entries(sheet.ranges).flatMap(([name, range]) => (fields.get(name) ? [[name, toDraft(fields.get(name)!, range, sheet.values[name])]] : []))),
  );
  const [bad, setBad] = useState<Record<string, boolean>>({});
  const [drop, setDrop] = useState<number[]>([]);
  const [check, setCheck] = useState<TeaCheck | null>(null);
  const [checking, setChecking] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [sending, setSending] = useState<"run" | "proposed" | "cancel" | null>(null);
  const [refused, setRefused] = useState<string | null>(null);
  const route = sheet.mode === "route";
  const visible = sheet.fields.filter((field) => field.editable);
  const pairedFields = new Set(sheet.paired?.fields ?? []);

  const parsed = useMemo(() => {
    const ranges: Record<string, TeaRange> = {};
    const counts: Record<string, number> = {};
    const errors: Record<string, string> = {};
    for (const [name, draft] of Object.entries(drafts)) {
      const field = fields.get(name);
      if (!field) continue;
      const out = fromDraft(field, draft);
      if ("error" in out) errors[name] = out.error;
      else {
        ranges[name] = out.range;
        counts[name] = out.count;
      }
    }
    return { ranges, counts, errors };
  }, [drafts, fields]);
  const blocked = Object.keys(bad).filter((name) => bad[name]);
  const draftErrors = Object.values(parsed.errors);

  useEffect(() => {
    setConfirming(false);
    if (blocked.length || draftErrors.length) {
      setCheck(null);
      return;
    }
    let live = true;
    setChecking(true);
    const timer = window.setTimeout(() => {
      api
        .teaCheck(sessionId, { sheet_id: sheet.id, values, ranges: parsed.ranges, drop })
        .then((report) => {
          if (live) setCheck(report);
        })
        .catch((e: Error) => {
          if (!live) return;
          if (e instanceof ApiError && e.status === 409) onGone();
          else setCheck({ ...NO_CHECK, error: e.message });
        })
        .finally(() => {
          if (live) setChecking(false);
        });
    }, 350);
    return () => {
      live = false;
      window.clearTimeout(timer);
    };
  }, [sessionId, sheet.id, values, parsed, drop, bad]); // blocked and draftErrors derive from bad and parsed

  const energyCases = parsed.ranges.energy_case?.kind === "list" ? parsed.ranges.energy_case.values : [values.energy_case];
  const utilitiesApply = energyCases.some((item) => item !== "C2");
  const edited = !same(values, sheet.values) || !same(parsed.ranges, sheet.ranges) || drop.length > 0 || Object.keys(parsed.errors).length > 0;

  const originOf = (name: string) => (drafts[name] && !sheet.ranges[name] ? "you" : !same(values[name], sheet.values[name]) ? "you" : sheet.origin[name] ?? "default");
  const setValue = (name: string, value: TeaValue, ok = true) => {
    setValues((current) => ({ ...current, [name]: value }));
    setBad((current) => ({ ...current, [name]: !ok }));
  };
  const setDraft = (name: string, draft: Draft | undefined) =>
    setDrafts((current) => {
      const next = { ...current };
      if (draft) next[name] = draft;
      else delete next[name];
      return next;
    });

  const [open, setOpen] = useState<Record<string, boolean>>(() => {
    const out: Record<string, boolean> = {};
    for (const group of sheet.groups) {
      out[group] = group === "Plant" || sheet.fields.some((f) => f.group === group && f.editable && (sheet.ranges[f.name] || !["default", "missing"].includes(sheet.origin[f.name] ?? "default")));
    }
    return out;
  });

  const submit = async (proposed: boolean) => {
    setSending(proposed ? "proposed" : "run");
    setRefused(null);
    try {
      const body = proposed
        ? { sheet_id: sheet.id, action: "run" as const, values: sheet.values, ranges: sheet.ranges, drop: [] }
        : { sheet_id: sheet.id, action: "run" as const, values, ranges: parsed.ranges, drop };
      const started = await api.teaAnswer(sessionId, body);
      onStarted(started.plants ?? 1);
    } catch (e) {
      if (e instanceof ApiError && e.status === 409) {
        notify(e.message, "error");
        onGone();
      } else if (e instanceof ApiError && e.status === 422) {
        const report = (e.detail as { report?: TeaCheck } | undefined)?.report;
        setRefused(report?.invalid.length ? `${e.message} ${report.invalid.slice(0, 3).map((row) => `${row.label}: ${row.error}`).join(" · ")}` : e.message);
      } else setRefused((e as Error).message);
    } finally {
      setSending(null);
    }
  };
  const cancel = async () => {
    setSending("cancel");
    try {
      await api.teaAnswer(sessionId, { sheet_id: sheet.id, action: "cancel" });
    } catch (e) {
      if (!(e instanceof ApiError && e.status === 409)) notify((e as Error).message, "error");
    } finally {
      setSending(null);
      onGone();
    }
  };

  const groups = route ? ["Plant"] : sheet.groups;
  const counted = check && !checking ? check : null;
  const plants = counted?.plants ?? 0;
  return (
    <>
      <div className="min-h-0 flex-1 overflow-y-auto px-4 pb-4">
        <p className="mt-3 font-headline text-[13px] leading-relaxed text-ink-2">
          {route
            ? "The model wants to cost this separation route. Check the plant scale, energy case and precipitation temperature; the route itself comes from the plan."
            : sheet.mode === "sensitivity"
              ? "The model wants a sensitivity run. Its sweep is already a range below; change it, range other fields, or edit the plant."
              : `${
                  sheet.opened === "reference"
                    ? "The stored reference plant (LDPE in dodecane, C1) with every default. Change any field"
                    : sheet.opened === "previous"
                      ? "Your last run in this chat. Change any field"
                      : "The model proposed this plant. Keep the values or change any field"
                }; the slider button turns a field into a range, and several ranges run as a grid.`}
        </p>
        {route && sheet.route && (
          <ol className="mt-3 space-y-1 rounded-lg border border-line bg-canvas px-3 py-2">
            {sheet.route.steps.map((step, index) => (
              <li key={index} className="font-headline text-sm text-ink">
                <span className="text-ink-2">Stage {index + 1}:</span> {step.polymer ?? "?"} in {step.solvent ?? "?"}
                {step.temperature_c !== null ? <span className="text-ink-2"> at {fmt(step.temperature_c, 4)} °C</span> : null}
              </li>
            ))}
          </ol>
        )}
        {sheet.paired && (
          <div className="mt-3 overflow-x-auto rounded-lg border border-line">
            <p className="border-b border-line bg-muted px-3 py-1.5 font-headline text-xs font-medium text-ink-2">The model proposed {sheet.paired.rows.length} plants; untick any to leave out</p>
            <table className="w-full font-headline text-[13px] tabular-nums">
              <thead>
                <tr className="text-left text-ink-2">
                  <th className="w-8 px-2 py-1" />
                  {sheet.paired.fields.map((name) => (
                    <th key={name} className="px-2 py-1 font-medium">
                      {fields.get(name)?.label ?? name}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {sheet.paired.rows.map((row, index) => (
                  <tr key={index} className="border-t border-line/70">
                    <td className="px-2 py-1">
                      <input
                        type="checkbox"
                        aria-label={`Plant ${index + 1}`}
                        checked={!drop.includes(index)}
                        onChange={() => setDrop(drop.includes(index) ? drop.filter((i) => i !== index) : [...drop, index])}
                        className="accent-[var(--primary-ink)]"
                      />
                    </td>
                    {row.map((value, i) => (
                      <td key={i} className="px-2 py-1 text-ink">
                        {plain(value, fields.get(sheet.paired!.fields[i])?.percent)}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        {Object.keys(sheet.extras).length > 0 && (
          <p className="mt-3 font-headline text-xs text-ink-2">Also sent by the model and kept as sent: {Object.keys(sheet.extras).join(", ")}.</p>
        )}
        {groups.map((group) => {
          const members = visible.filter((field) => (route ? true : field.group === group));
          if (!members.length) return null;
          if (!route && group === "Utilities" && !utilitiesApply) {
            return (
              <p key={group} className="mt-3 font-headline text-xs text-ink-2">
                Utilities: energy case C2 buys grid electricity and runs no boiler, so the natural gas price and the steam plant's depreciation do not apply.
              </p>
            );
          }
          const changed = members.filter((field) => drafts[field.name] || (field.name in sheet.defaults && !same(values[field.name], sheet.defaults[field.name]))).length;
          const expanded = open[group] ?? false;
          return (
            <section key={group} className="mt-3 rounded-lg border border-line bg-canvas">
              <button
                type="button"
                onClick={() => setOpen({ ...open, [group]: !expanded })}
                aria-expanded={expanded}
                className="flex w-full items-center gap-2 px-3 py-2 text-left"
              >
                <ChevronDown size={14} className={cx("text-ink-2 transition-transform", !expanded && "-rotate-90")} />
                <span className="flex-1 font-headline text-sm font-semibold text-ink">{route ? "Route plant" : group}</span>
                <span className="font-headline text-xs text-ink-2">
                  {members.length} field{members.length === 1 ? "" : "s"}
                  {changed ? ` · ${changed} differ${changed === 1 ? "s" : ""} from the defaults` : " · defaults"}
                </span>
              </button>
              {expanded && (
                <div className="px-3 pb-1">
                  {members.map((field) => (
                    <FieldRow
                      key={field.name}
                      field={field}
                      value={values[field.name] ?? null}
                      origin={originOf(field.name)}
                      fallback={sheet.defaults[field.name]}
                      hasDefault={field.name in sheet.defaults}
                      draft={drafts[field.name]}
                      rangeCount={parsed.counts[field.name] ?? null}
                      rangeError={parsed.errors[field.name]}
                      paired={pairedFields.has(field.name)}
                      invalid={Boolean(bad[field.name])}
                      suggestions={field.name === "target_polymer" ? sheet.polymers : field.name === "solvent" ? sheet.solvents.map((s) => s.name) : undefined}
                      note={
                        field.name === "solvent_price_usd_per_kg" &&
                        counted?.admitted_price != null &&
                        typeof values.solvent_price_usd_per_kg === "number" &&
                        Math.abs(counted.admitted_price - values.solvent_price_usd_per_kg) > 1e-9 ? (
                          <p className="mt-0.5 font-headline text-[11px] text-ink-2">
                            The solvent table prices {String(values.solvent)} at {fmt(counted.admitted_price, 4)} USD/kg.{" "}
                            <button type="button" onClick={() => setValue("solvent_price_usd_per_kg", counted.admitted_price ?? null)} className="text-brand-ink underline underline-offset-2">
                              Use it
                            </button>
                          </p>
                        ) : field.only_c1_c3 && energyCases.includes("C2") ? (
                          <p className="mt-0.5 font-headline text-[11px] text-ink-2">Applies to the C1 and C3 plants only.</p>
                        ) : null
                      }
                      onValue={(value, ok) => setValue(field.name, value, ok ?? true)}
                      onDraft={(draft) => setDraft(field.name, draft)}
                    />
                  ))}
                </div>
              )}
            </section>
          );
        })}
      </div>
      <footer className="border-t border-line bg-surface px-4 py-3">
        <div className="min-h-[2.5rem] font-headline text-[13px]" aria-live="polite">
          {blocked.length > 0 ? (
            <p className="text-bad">Fix {blocked.map((name) => fields.get(name)?.label ?? name).join(", ")}: not a number yet.</p>
          ) : draftErrors.length > 0 ? (
            <p className="text-bad">{draftErrors[0]}</p>
          ) : checking || !check ? (
            <p className="flex items-center gap-1.5 text-ink-2">
              <Loader2 size={13} className="animate-spin" /> Checking the plants…
            </p>
          ) : check.error ? (
            <p className="text-bad">{check.error}</p>
          ) : check.invalid_count > 0 ? (
            <div className="text-bad">
              <p className="font-medium">
                {check.invalid_count} of {check.plants} plant{check.plants === 1 ? "" : "s"} would be refused:
              </p>
              <ul className="mt-0.5 max-h-20 overflow-y-auto text-xs">
                {check.invalid.slice(0, 6).map((row, index) => (
                  <li key={index}>
                    {check.plants > 1 ? <span className="font-medium">{row.label}: </span> : null}
                    {row.error}
                  </li>
                ))}
              </ul>
            </div>
          ) : (
            <p className="text-ink">
              <span className="font-semibold">
                {check.plants.toLocaleString("en-US")} plant{check.plants === 1 ? "" : "s"}
              </span>
              <span className="text-ink-2">
                {route
                  ? ` · ${check.live} stage run${check.live === 1 ? "" : "s"}, up to ${duration(check.seconds)} if none is stored`
                  : ` · ${check.stored ? `${check.stored} stored (instant) · ` : ""}${check.live ? `${check.live} live, about ${duration(check.seconds)}` : "no live run"}`}
              </span>
            </p>
          )}
          {refused && <p className="mt-1 text-bad">{refused}</p>}
        </div>
        <div className="mt-2 flex flex-wrap items-center gap-2">
          <button type="button" onClick={() => void cancel()} disabled={sending !== null} className="rounded-lg px-3 py-2 font-headline text-sm text-ink-2 hover:bg-muted hover:text-ink">
            Cancel
          </button>
          <span className="flex-1" />
          {edited && (
            <button
              type="button"
              onClick={() => void submit(true)}
              disabled={sending !== null}
              title="Run the model's values without your changes"
              className="rounded-lg bg-muted px-3 py-2 font-headline text-sm text-ink hover:bg-line disabled:opacity-60"
            >
              {sending === "proposed" ? <Loader2 size={14} className="animate-spin" /> : "Run as proposed"}
            </button>
          )}
          <button
            type="button"
            disabled={!counted?.runnable || sending !== null || blocked.length > 0}
            onClick={() => {
              if (counted?.confirm && !confirming) setConfirming(true);
              else void submit(false);
            }}
            className="flex items-center gap-1.5 rounded-lg bg-brand px-3.5 py-2 font-headline text-sm font-semibold text-on-brand hover:bg-brand-hover disabled:cursor-not-allowed disabled:opacity-50"
          >
            {sending === "run" ? <Loader2 size={14} className="animate-spin" /> : <Play size={14} />}
            {confirming && counted
              ? `Confirm ${plants.toLocaleString("en-US")} plants${counted.live ? `, about ${duration(counted.seconds)}` : ""}`
              : plants > 1
                ? `Run ${plants.toLocaleString("en-US")} plants`
                : "Run"}
          </button>
        </div>
      </footer>
    </>
  );
}

function ResultTable({ result, rows }: { result: TeaResult; rows: TeaRow[] }) {
  const [sort, setSort] = useState<{ key: string; dir: 1 | -1 } | null>(null);
  const columns: { key: string; label: string; value: (row: TeaRow) => number | string | null }[] = [
    ...result.fields.map((field) => ({
      key: field.name,
      label: `${field.label}${field.percent ? " (%)" : field.unit && field.kind === "number" ? ` (${field.unit})` : ""}`,
      value: (row: TeaRow) => {
        const v = row.values[field.name];
        return typeof v === "number" ? (field.percent ? v * 100 : v) : v === null || v === undefined ? null : plain(v);
      },
    })),
    ...METRICS.map((metric) => ({ key: metric.key, label: `${metric.label} (${metric.unit})`, value: (row: TeaRow) => row[metric.key] ?? null })),
  ];
  const sorted = useMemo(() => {
    if (!sort) return rows;
    const column = columns.find((c) => c.key === sort.key);
    if (!column) return rows;
    return [...rows].sort((a, b) => {
      const x = column.value(a);
      const y = column.value(b);
      if (x === null) return 1;
      if (y === null) return -1;
      return (typeof x === "number" && typeof y === "number" ? x - y : String(x).localeCompare(String(y))) * sort.dir;
    });
  }, [rows, sort, result]); // columns derive from result
  return (
    <div className="overflow-x-auto rounded-lg border border-line">
      <table className="w-full font-headline text-[12.5px] tabular-nums">
        <thead className="bg-muted text-left text-ink-2">
          <tr>
            {columns.map((column) => (
              <th key={column.key} className="whitespace-nowrap px-2 py-1.5 font-medium">
                <button
                  type="button"
                  onClick={() => setSort({ key: column.key, dir: sort?.key === column.key ? (-sort.dir as 1 | -1) : 1 })}
                  className="hover:text-ink"
                  aria-label={`Sort by ${column.label}`}
                >
                  {column.label}
                  {sort?.key === column.key ? (sort.dir === 1 ? " ↑" : " ↓") : ""}
                </button>
              </th>
            ))}
            <th className="whitespace-nowrap px-2 py-1.5 font-medium">Basis</th>
          </tr>
        </thead>
        <tbody>
          {sorted.map((row) => (
            <tr key={row.index} className={cx("border-t border-line/70", !row.success && "bg-bad/5")}>
              {columns.map((column) => {
                const v = column.value(row);
                return (
                  <td key={column.key} className="whitespace-nowrap px-2 py-1 text-ink">
                    {typeof v === "number" ? (column.key in row.values ? fmt(v, 6) : fmt(v, 3, true)) : (v ?? "–")}
                  </td>
                );
              })}
              <td className="px-2 py-1 text-ink-2">
                {row.success ? (
                  row.engine_mode === "cache" ? (
                    <span title={row.stored_record ?? undefined}>stored</span>
                  ) : (
                    (row.engine_mode ?? "–")
                  )
                ) : (
                  <span className="text-bad" title={row.error}>
                    refused{row.error_code ? `: ${row.error_code}` : ""}
                  </span>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function ticks(lo: number, hi: number, count = 5): number[] {
  if (lo === hi) {
    const pad = Math.abs(lo) * 0.1 || 1;
    lo -= pad;
    hi += pad;
  }
  const raw = (hi - lo) / count;
  const magnitude = 10 ** Math.floor(Math.log10(raw));
  const ratio = raw / magnitude;
  const step = (ratio >= 7.5 ? 10 : ratio >= 3.5 ? 5 : ratio >= 1.5 ? 2 : 1) * magnitude;
  const out: number[] = [];
  for (let v = Math.floor(lo / step) * step; v <= hi + step * 0.5 && out.length < 12; v += step) out.push(clean(v));
  if (out[out.length - 1] < hi) out.push(clean(out[out.length - 1] + step));
  return out;
}

/** The metric against the first numeric ranged field, one line per value of a second ranged field. */
function Chart({ result, metric }: { result: TeaResult; metric: MetricKey }) {
  const meta = new Map(result.fields.map((field) => [field.name, field]));
  const names = result.axes.map((axis) => axis.name);
  const xName = names.find((name) => meta.get(name)?.kind === "number") ?? names[0];
  const seriesName = names.find((name) => name !== xName);
  const xField = meta.get(xName);
  if (!xField) return null;
  const numeric = xField.kind === "number";
  const categories = (result.axes.find((axis) => axis.name === xName)?.values ?? []).map((v) => plain(v, xField.percent));
  const points = result.rows
    .filter((row) => row.success && typeof row[metric] === "number")
    .map((row) => {
      const raw = row.values[xName];
      const x = numeric && typeof raw === "number" ? (xField.percent ? raw * 100 : raw) : categories.indexOf(plain(raw, xField.percent));
      return { row, x, y: row[metric] as number, series: seriesName ? plain(row.values[seriesName], meta.get(seriesName)?.percent) : "" };
    })
    .filter((point) => Number.isFinite(point.x) && (numeric || point.x >= 0));
  if (points.length < 2) return <p className="font-headline text-xs text-ink-2">Too few plants came back to draw a line.</p>;
  const W = 440;
  const H = 230;
  const [L, R, T, B] = [52, 26, 10, 38];
  const xs = points.map((p) => p.x);
  const [xMin, xMax] = [Math.min(...xs), Math.max(...xs)];
  const log = numeric && xMin > 0 && xMax / xMin >= 100;
  const yTicks = ticks(Math.min(...points.map((p) => p.y)), Math.max(...points.map((p) => p.y)));
  const [yLo, yHi] = [yTicks[0], yTicks[yTicks.length - 1]];
  const px = (x: number) => {
    if (!numeric) return L + ((W - L - R) * (x + 0.5)) / Math.max(categories.length, 1);
    if (xMax === xMin) return (L + W - R) / 2;
    const t = log ? (Math.log10(x) - Math.log10(xMin)) / (Math.log10(xMax) - Math.log10(xMin)) : (x - xMin) / (xMax - xMin);
    return L + t * (W - L - R);
  };
  const py = (y: number) => T + (1 - (y - yLo) / (yHi - yLo || 1)) * (H - T - B);
  const xTicks = !numeric
    ? categories.map((_label, index) => index)
    : log
      ? Array.from({ length: Math.floor(Math.log10(xMax)) - Math.ceil(Math.log10(xMin)) + 1 }, (_v, i) => 10 ** (Math.ceil(Math.log10(xMin)) + i))
      : ticks(xMin, xMax, 4).filter((v) => v >= xMin - 1e-9 && v <= xMax + 1e-9);
  const groups = [...new Set(points.map((p) => p.series))];
  const unit = METRICS.find((m) => m.key === metric)!;
  const seriesLabel = seriesName ? (result.labels[seriesName] ?? seriesName) : "";
  return (
    <figure>
      <figcaption className="mb-1 font-headline text-xs text-ink-2">
        {unit.label} ({unit.unit}) against {xField.label.toLowerCase()}
        {log ? " (log scale)" : ""}
      </figcaption>
      <svg viewBox={`0 0 ${W} ${H}`} className="h-auto w-full" role="img" aria-label={`${unit.label} against ${xField.label}`}>
        {yTicks.map((v) => (
          <g key={`y${v}`}>
            <line x1={L} x2={W - R} y1={py(v)} y2={py(v)} stroke="var(--border-color)" strokeWidth={1} />
            <text x={L - 6} y={py(v)} textAnchor="end" dominantBaseline="middle" fontSize={10.5} fill="var(--text-secondary)" fontFamily="Inter, sans-serif">
              {fmt(v)}
            </text>
          </g>
        ))}
        {xTicks.map((v) => (
          <text key={`x${v}`} x={px(v)} y={H - B + 14} textAnchor={px(v) > W - R - 12 ? "end" : "middle"} fontSize={10.5} fill="var(--text-secondary)" fontFamily="Inter, sans-serif">
            {numeric ? fmt(v) : categories[v]}
          </text>
        ))}
        <text x={(L + W - R) / 2} y={H - 6} textAnchor="middle" fontSize={11} fill="var(--text-secondary)" fontFamily="Inter, sans-serif">
          {xField.label}
          {xField.percent ? " (%)" : xField.unit ? ` (${xField.unit})` : ""}
        </text>
        {groups.map((group, gi) => {
          const line = points.filter((p) => p.series === group).sort((a, b) => a.x - b.x);
          const color = SERIES[gi % SERIES.length];
          return (
            <g key={group || "all"}>
              <polyline points={line.map((p) => `${px(p.x)},${py(p.y)}`).join(" ")} fill="none" stroke={color} strokeWidth={2} strokeLinejoin="round" />
              {line.map((p) => (
                <circle key={p.row.index} cx={px(p.x)} cy={py(p.y)} r={3.2} fill={color}>
                  <title>{`${p.row.label}: ${unit.label} ${fmt(p.y, 3, true)} ${unit.unit}`}</title>
                </circle>
              ))}
            </g>
          );
        })}
      </svg>
      {seriesName && (
        <div className="mt-1 flex flex-wrap gap-x-3 gap-y-1 font-headline text-xs text-ink">
          {groups.map((group, gi) => (
            <span key={group} className="flex items-center gap-1.5">
              <span className="h-2 w-3 rounded-sm" style={{ backgroundColor: SERIES[gi % SERIES.length] }} />
              {seriesLabel} {group}
            </span>
          ))}
        </div>
      )}
    </figure>
  );
}

function Metric({ label, value, unit, detail }: { label: string; value: number | null | undefined; unit: string; detail?: string }) {
  return (
    <div className="rounded-lg border border-line bg-canvas px-3 py-2">
      <span className="block font-headline text-[11px] font-medium uppercase tracking-wide text-ink-2">{label}</span>
      <span className="block font-headline text-lg font-semibold tabular-nums text-ink">
        {fmt(value, 3, true)} <span className="text-xs font-normal text-ink-2">{unit}</span>
      </span>
      {detail && <span className="block font-headline text-[11px] text-ink-2">{detail}</span>}
    </div>
  );
}

function csv(result: TeaResult): string {
  const names = result.fields.map((field) => field.name);
  const header = ["label", ...names, ...METRICS.map((m) => m.key), "electricity_mj_per_kg", "heating_mj_per_kg", "cooling_mj_per_kg", "engine_mode", "stored_record", "success", "error"];
  const cell = (value: unknown) => {
    const text = value === null || value === undefined ? "" : Array.isArray(value) ? value.join(" ") : String(value);
    return /[",\n]/.test(text) ? `"${text.replaceAll('"', '""')}"` : text;
  };
  const lines = result.rows.map((row) =>
    [row.label, ...names.map((name) => row.values[name]), ...METRICS.map((m) => row[m.key]), row.electricity_mj_per_kg, row.heating_mj_per_kg, row.cooling_mj_per_kg, row.engine_mode, row.stored_record, row.success, row.error]
      .map(cell)
      .join(","),
  );
  return [header.join(","), ...lines].join("\n");
}

function Results({ result, running }: { result: TeaResult; running?: TeaProgress | null }) {
  const [metric, setMetric] = useState<MetricKey>("msp_usd_per_kg");
  const [showDefaults, setShowDefaults] = useState(false);
  const rows = result.rows;
  const ok = rows.filter((row) => row.success);
  const axes = result.axes.filter((axis) => result.fields.some((f) => f.name === axis.name));
  const chartable = !result.paired_fields.length && axes.length >= 1 && axes.length <= 2 && ok.length >= 2;
  const single = rows.length === 1;
  const stored = ok.filter((row) => row.engine_mode === "cache").length;
  const label = (name: string) => result.labels[name] ?? name;
  const download = () => {
    const blob = new Blob([csv(result)], { type: "text/csv" });
    const link = document.createElement("a");
    link.href = URL.createObjectURL(blob);
    link.download = `dissolve-tea-${result.sheet_id}.csv`;
    link.click();
    URL.revokeObjectURL(link.href);
  };
  return (
    <div className="min-h-0 flex-1 space-y-3 overflow-y-auto px-4 py-3">
      <p className="font-headline text-[13px] text-ink">
        {result.stopped ? (
          <span className="font-semibold text-warn">Stopped after {result.ran} of {result.planned} plants. </span>
        ) : null}
        {ok.length} of {rows.length} {result.mode === "route" ? "stage" : "plant"}
        {rows.length === 1 ? "" : "s"} came back
        {ok.length ? ` · ${stored ? `${stored} stored design point${stored === 1 ? "" : "s"}` : ""}${stored && ok.length - stored ? " · " : ""}${ok.length - stored ? `${ok.length - stored} live BioSTEAM run${ok.length - stored === 1 ? "" : "s"}` : ""}` : ""}.
        {running ? " More are running." : " The model reads the same rows to answer."}
      </p>
      {single && ok.length === 1 ? (
        <div className="grid grid-cols-2 gap-2">
          <Metric label="Minimum selling price" value={ok[0].msp_usd_per_kg} unit="USD/kg" />
          <Metric label="Global warming potential" value={ok[0].gwp_kg_co2e_per_kg} unit="kg CO₂e/kg" />
          <Metric label="Total capital investment" value={ok[0].tci_usd} unit="USD" />
          <Metric label="Annual operating cost" value={ok[0].aoc_usd_per_yr} unit="USD/yr" />
          <div className="col-span-2">
            <Metric
              label="Energy"
              value={ok[0].total_energy_mj_per_kg}
              unit="MJ/kg"
              detail={`electricity ${fmt(ok[0].electricity_mj_per_kg, 3, true)} · heating ${fmt(ok[0].heating_mj_per_kg, 3, true)} · cooling ${fmt(ok[0].cooling_mj_per_kg, 3, true)} MJ/kg`}
            />
          </div>
          <p className="col-span-2 font-headline text-xs text-ink-2">
            {ok[0].engine_mode === "cache" ? `A stored design point (${ok[0].stored_record ?? "record"}), not a new run.` : "A live BioSTEAM run of this plant."}
            {ok[0].can_cite_as_validated_process === true ? " It can be cited as the validated process." : ok[0].can_cite_as_validated_process === false ? " It cannot be cited as the validated process." : ""}
          </p>
        </div>
      ) : (
        <>
          {chartable && (
            <div className="rounded-lg border border-line bg-canvas p-3">
              <div className="mb-2 flex flex-wrap gap-1">
                {METRICS.map((m) => (
                  <button
                    key={m.key}
                    type="button"
                    onClick={() => setMetric(m.key)}
                    aria-pressed={metric === m.key}
                    className={cx("rounded-full px-2.5 py-0.5 font-headline text-xs", metric === m.key ? "bg-brand text-on-brand" : "bg-muted text-ink hover:bg-line")}
                  >
                    {m.label}
                  </button>
                ))}
              </div>
              <Chart result={result} metric={metric} />
            </div>
          )}
          <ResultTable result={result} rows={rows} />
        </>
      )}
      {rows.some((row) => !row.success) && (
        <div className="rounded-lg border border-bad/30 bg-bad/5 px-3 py-2 font-headline text-xs text-bad">
          {rows
            .filter((row) => !row.success)
            .slice(0, 4)
            .map((row) => (
              <p key={row.index}>
                <span className="font-medium">{row.label}:</span> {row.error}
              </p>
            ))}
        </div>
      )}
      <div className="space-y-1 font-headline text-xs text-ink-2">
        {result.edited_fields.length > 0 && <p>You changed: {result.edited_fields.map(label).join(", ")}.</p>}
        {result.defaulted_fields.length > 0 && (
          <p>
            {result.defaulted_fields.length} field{result.defaulted_fields.length === 1 ? "" : "s"} ran at the first-run defaults.{" "}
            <button type="button" onClick={() => setShowDefaults(!showDefaults)} className="text-brand-ink underline underline-offset-2">
              {showDefaults ? "Hide" : "Show"}
            </button>
            {showDefaults && <span className="mt-1 block">{result.defaulted_fields.map(label).join(", ")}</span>}
          </p>
        )}
      </div>
      {rows.length > 1 && (
        <button type="button" onClick={download} className="flex items-center gap-1.5 rounded-lg bg-muted px-3 py-1.5 font-headline text-sm text-ink hover:bg-line">
          <Download size={14} /> Download CSV
        </button>
      )}
    </div>
  );
}

function RunView({ sessionId, view, notify }: { sessionId: string; view: TeaView; notify: (text: string, kind?: "info" | "error") => void }) {
  const [stopping, setStopping] = useState(false);
  const progress = view.progress;
  if (!view.followed) {
    return (
      <div className="flex-1 px-4 py-6 font-headline text-sm text-ink-2">
        <p className="flex items-center gap-2 text-ink">
          <Loader2 size={15} className="animate-spin" /> Running on the server.
        </p>
        <p className="mt-2">This page opened after the question was asked, so the progress does not stream here. The answer lands in this chat when the run finishes; open the chat again to see it.</p>
      </div>
    );
  }
  const done = progress?.done ?? 0;
  const total = progress?.total ?? 0;
  const stop = async () => {
    if (!progress) return;
    setStopping(true);
    try {
      await api.teaAnswer(sessionId, { sheet_id: progress.sheet_id, action: "stop" });
    } catch (e) {
      notify((e as Error).message, "error");
      setStopping(false);
    }
  };
  const partial: TeaResult | null =
    progress && progress.rows.length > 0 && view.sheet
      ? {
          sheet_id: progress.sheet_id,
          mode: view.sheet.mode,
          title: view.sheet.title,
          rows: progress.rows,
          axes: [],
          paired_fields: [],
          planned: total,
          ran: done,
          stopped: false,
          edited_fields: [],
          defaulted_fields: [],
          fields: view.sheet.fields.filter((f) => progress.rows.some((row) => f.name in row.values)),
          labels: Object.fromEntries(view.sheet.fields.map((f) => [f.name, f.label])),
        }
      : null;
  return (
    <>
      <div className="border-b border-line px-4 py-3">
        <div className="flex items-center justify-between font-headline text-sm text-ink">
          <span className="flex items-center gap-2">
            <Loader2 size={14} className="animate-spin text-brand-ink" />
            {total > 1 ? `Plant ${Math.min(done + 1, total)} of ${total}` : "Running the plant"}
          </span>
          <button
            type="button"
            onClick={() => void stop()}
            disabled={stopping || !progress}
            className="flex items-center gap-1 rounded-lg bg-muted px-2.5 py-1 font-headline text-xs text-ink hover:bg-line disabled:opacity-60"
          >
            <Square size={11} /> {stopping ? "Stopping after this plant…" : "Stop"}
          </button>
        </div>
        <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-muted" role="progressbar" aria-valuemin={0} aria-valuemax={total} aria-valuenow={done}>
          <div className="h-full rounded-full bg-brand transition-all" style={{ width: `${total ? (100 * done) / total : 0}%` }} />
        </div>
        {progress?.running && <p className="mt-1.5 truncate font-mono text-[11px] text-ink-2" title={progress.running}>{progress.running}</p>}
      </div>
      {partial ? <Results result={partial} running={progress} /> : <div className="flex-1" />}
    </>
  );
}

/** The panel: a drawer beside the chat on wide screens, a bottom sheet on small ones. */
export function TeaPanel({ sessionId, view, onView, onClose, notify }: {
  sessionId: string;
  view: TeaView;
  onView: (next: TeaView | null | ((current: TeaView | null) => TeaView | null)) => void;
  onClose: () => void;
  notify: (text: string, kind?: "info" | "error") => void;
}) {
  const sheet = view.sheet;
  const title = view.result?.title ?? sheet?.title ?? "TEA";
  const mode = view.result?.mode ?? sheet?.mode ?? "evaluate";
  const pending = view.phase === "sheet" ? sheet : null;
  const close = async () => {
    if (pending) {
      try {
        await api.teaAnswer(sessionId, { sheet_id: pending.id, action: "cancel" });
      } catch (e) {
        if (!(e instanceof ApiError && e.status === 409)) notify((e as Error).message, "error");
      }
      onView(null);
    }
    onClose();
  };
  return (
    <>
      <div className="fixed inset-0 z-30 bg-black/40 lg:hidden" aria-hidden onClick={pending ? undefined : onClose} />
      <aside
        aria-label="TEA panel"
        className="rise fixed inset-x-0 bottom-0 z-40 flex h-[85dvh] flex-col rounded-t-2xl border-t border-line bg-surface shadow-float lg:static lg:z-auto lg:h-auto lg:w-[460px] lg:shrink-0 lg:rounded-none lg:border-l lg:border-t-0 lg:shadow-none"
      >
        <header className="flex items-center gap-2.5 border-b border-line px-4 py-3">
          <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-brand-tint text-brand-ink">
            {view.phase === "done" ? <BarChart3 size={16} /> : <Calculator size={16} />}
          </span>
          <div className="min-w-0 flex-1">
            <h2 className="truncate font-headline text-[15px] font-semibold text-ink">{title}</h2>
            <p className="font-headline text-xs text-ink-2">
              {view.phase === "sheet" ? "Confirm the plant" : view.phase === "running" ? "Running" : "Results"} ·{" "}
              {mode === "sensitivity" ? `sensitivity${sheet?.sensitivity ? ` of ${sheet.fields.find((f) => f.name === sheet.sensitivity?.parameter)?.label.toLowerCase() ?? sheet.sensitivity.parameter}` : ""}` : mode === "route" ? "separation route" : "plant TEA"}
            </p>
          </div>
          <button
            type="button"
            onClick={() => void close()}
            aria-label={pending ? "Close without running" : "Close the panel"}
            title={pending ? "Close without running: the model answers without TEA numbers" : "Close the panel"}
            className="rounded-lg p-1.5 text-ink-2 hover:bg-muted hover:text-ink"
          >
            <X size={18} />
          </button>
        </header>
        {pending ? (
          <SheetEditor
            key={pending.id}
            sessionId={sessionId}
            sheet={pending}
            notify={notify}
            onGone={() => {
              onView(null);
              onClose();
            }}
            onStarted={(plants) =>
              onView((current) =>
                current?.phase === "sheet" && current.sheet?.id === pending.id
                  ? { ...current, phase: "running", progress: { sheet_id: pending.id, done: 0, total: plants, rows: [], running: null } }
                  : current,
              )
            }
          />
        ) : view.phase === "running" ? (
          <RunView sessionId={sessionId} view={view} notify={notify} />
        ) : view.result ? (
          <Results result={view.result} />
        ) : (
          <div className="flex-1" />
        )}
      </aside>
    </>
  );
}

/** The chat's line for a TEA in the running answer: waiting for the person, running, or finished with results. */
export function TeaNotice({ state, progress, result, onOpen }: { state?: "waiting" | "running"; progress?: string; result?: TeaResult; onOpen: () => void }) {
  if (!state && !result) return null;
  const text =
    state === "waiting"
      ? "Waiting for you to confirm the plant in the TEA panel."
      : state === "running"
        ? `Running the TEA${progress ? ` · ${progress}` : ""}.`
        : `TEA results · ${result!.ran} ${result!.mode === "route" ? "route run" : `plant${result!.ran === 1 ? "" : "s"}`}${result!.stopped ? " (stopped early)" : ""}.`;
  return (
    <div className={cx("mb-3 flex items-center gap-2 rounded-xl border px-3 py-2 font-headline text-[13px]", state === "waiting" ? "border-brand/40 bg-brand-tint text-ink" : "border-line bg-canvas/60 text-ink")}>
      {state ? <Loader2 size={14} className="shrink-0 animate-spin text-brand-ink" /> : <BarChart3 size={14} className="shrink-0 text-brand-ink" />}
      <span className="flex-1">{text}</span>
      <button type="button" onClick={onOpen} className="shrink-0 rounded-lg bg-brand px-2.5 py-1 font-headline text-xs font-semibold text-on-brand hover:bg-brand-hover">
        {state === "waiting" ? "Open the panel" : state === "running" ? "Show progress" : "Open results"}
      </button>
    </div>
  );
}
