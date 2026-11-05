import {
  Check,
  Database,
  FileCheck2,
  LineChart,
  Loader2,
  Minus,
  Newspaper,
  PenLine,
  X,
  type LucideIcon,
} from "lucide-react";
import { useEffect, useState } from "react";

import { seconds } from "../format";
import type { StepEvent } from "../types";

export interface StepInfo {
  key: string;
  label: string;
  role: string;
  working: string;
  icon: LucideIcon;
}

export const STEPS: StepInfo[] = [
  {
    key: "collect_data",
    label: "Data collection",
    role: "Quote, profile, TTM metrics and recent news",
    working: "Fetching market data and the week's headlines…",
    icon: Database,
  },
  {
    key: "market_analyst",
    label: "Market analyst",
    role: "Turns the numbers into strengths and concerns",
    working: "Reading the financials…",
    icon: LineChart,
  },
  {
    key: "news_analyst",
    label: "News analyst",
    role: "Screens headlines, reads articles, ranks developments",
    working: "Screening headlines and reading the most relevant articles…",
    icon: Newspaper,
  },
  {
    key: "report_writer",
    label: "Report writer",
    role: "Writes the balanced executive narrative",
    working: "Drafting the briefing from both analyses…",
    icon: PenLine,
  },
  {
    key: "finalize",
    label: "Finalize",
    role: "Assembles figures and verified source links",
    working: "Assembling the document…",
    icon: FileCheck2,
  },
];

interface Props {
  steps: Record<string, StepEvent>;
  ticker: string | null;
  companyName: string | null;
  queued: boolean;
  startedAt: number;
}

export function AgentPipeline({ steps, ticker, companyName, queued, startedAt }: Props) {
  const elapsed = useElapsed(startedAt);
  const headline = ticker
    ? `Researching ${companyName ? `${companyName} (${ticker})` : ticker}`
    : "Understanding your request";

  return (
    <section className="panel overflow-hidden" aria-live="polite">
      <div className="flex items-center justify-between border-b border-ink-700/70 px-5 py-4">
        <div className="flex items-center gap-3">
          <Loader2 size={18} className="animate-spin text-accent" />
          <div>
            <h2 className="text-[15px] font-semibold text-white">{headline}</h2>
            <p className="text-xs text-ink-400">
              {queued
                ? "Another briefing is running. You're next in line."
                : "Agents are working. A full briefing takes about a minute."}
            </p>
          </div>
        </div>
        <span className="num text-sm text-ink-300">{elapsed}s</span>
      </div>
      <div className="relative h-px overflow-hidden bg-ink-800">
        <div className="absolute inset-y-0 w-1/3 animate-scan bg-gradient-to-r from-transparent via-accent to-transparent" />
      </div>
      <ol className="divide-y divide-ink-800/80">
        {STEPS.map((info, index) => (
          <StepRow key={info.key} info={info} event={steps[info.key]} parallel={index === 1 || index === 2} />
        ))}
      </ol>
    </section>
  );
}

function StepRow({ info, event, parallel }: { info: StepInfo; event?: StepEvent; parallel: boolean }) {
  const status = event?.status;
  const Icon = info.icon;
  const tone =
    status === "running"
      ? "text-accent"
      : status === "succeeded"
        ? "text-up"
        : status === "failed"
          ? "text-down"
          : "text-ink-500";

  return (
    <li className={`flex items-start gap-4 px-5 py-3.5 transition ${status ? "" : "opacity-55"}`}>
      <div
        className={`mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-lg border ${
          status === "running" ? "border-accent/50 bg-accent/10" : "border-ink-700 bg-ink-850"
        }`}
      >
        <Icon size={16} className={tone} />
      </div>
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
          <span className="text-sm font-medium text-ink-100">{info.label}</span>
          {parallel && (
            <span className="rounded border border-ink-700 px-1.5 py-px font-mono text-[10px] uppercase tracking-wider text-ink-400">
              parallel
            </span>
          )}
        </div>
        <p className="mt-0.5 truncate text-xs text-ink-400">
          {status === "running"
            ? info.working
            : status === "succeeded" || status === "failed" || status === "skipped"
              ? describe(event!)
              : info.role}
        </p>
      </div>
      <div className="flex shrink-0 items-center gap-2 pt-1">
        {event?.duration_ms != null && (
          <span className="num text-xs text-ink-400">{seconds(event.duration_ms)}</span>
        )}
        <StatusIcon status={status} />
      </div>
    </li>
  );
}

function describe(event: StepEvent): string {
  if (event.status === "skipped") return "Skipped: no input for this step";
  const parts = [event.detail ?? ""];
  if (event.llm_calls) parts.push(`${event.llm_calls} model call${event.llm_calls > 1 ? "s" : ""}`);
  if (event.tool_calls.length) parts.push(`tools: ${[...new Set(event.tool_calls)].join(", ")}`);
  return parts.filter(Boolean).join(" · ");
}

function StatusIcon({ status }: { status?: string }) {
  if (status === "running") return <Loader2 size={16} className="animate-spin text-accent" />;
  if (status === "succeeded") return <Check size={16} className="text-up" />;
  if (status === "failed") return <X size={16} className="text-down" />;
  if (status === "skipped") return <Minus size={16} className="text-ink-400" />;
  return <span className="h-1.5 w-1.5 rounded-full bg-ink-600" />;
}

function useElapsed(startedAt: number): string {
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), 250);
    return () => clearInterval(id);
  }, []);
  return ((now - startedAt) / 1000).toFixed(0);
}
