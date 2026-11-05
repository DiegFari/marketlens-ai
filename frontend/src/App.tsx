import { AlertTriangle, ArrowLeft, HelpCircle } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";

import { fetchExample, fetchExamples, fetchQuota, streamBriefing } from "./api";
import { AgentPipeline } from "./components/AgentPipeline";
import { BriefingView } from "./components/BriefingView";
import { Composer } from "./components/Composer";
import { Examples } from "./components/Examples";
import { Footer, Header } from "./components/Chrome";
import { HowItWorks } from "./components/HowItWorks";
import type { Briefing, ExampleSummary, Quota, StepEvent, StreamEvent } from "./types";

type Phase = "idle" | "working" | "done" | "clarify" | "error";
export type Origin = "live" | "cached" | "example";

interface Run {
  query: string;
  ticker: string | null;
  companyName: string | null;
  steps: Record<string, StepEvent>;
  queued: boolean;
  startedAt: number;
}

const emptyRun = (query: string): Run => ({
  query,
  ticker: null,
  companyName: null,
  steps: {},
  queued: false,
  startedAt: Date.now(),
});

export default function App() {
  const [phase, setPhase] = useState<Phase>("idle");
  const [run, setRun] = useState<Run | null>(null);
  const [briefing, setBriefing] = useState<Briefing | null>(null);
  const [origin, setOrigin] = useState<Origin>("live");
  const [message, setMessage] = useState<{ text: string; code?: string } | null>(null);
  const [examples, setExamples] = useState<ExampleSummary[]>([]);
  const [quota, setQuota] = useState<Quota | null>(null);
  const abort = useRef<AbortController | null>(null);
  const finished = useRef(false);
  const top = useRef<HTMLDivElement>(null);

  const refreshQuota = useCallback(() => {
    fetchQuota().then(setQuota).catch(() => setQuota(null));
  }, []);

  useEffect(() => {
    fetchExamples().then(setExamples).catch(() => setExamples([]));
    refreshQuota();
  }, [refreshQuota]);

  const setLink = (example: string | null) => {
    const url = new URL(window.location.href);
    if (example) url.searchParams.set("example", example);
    else url.searchParams.delete("example");
    window.history.replaceState(null, "", url);
  };

  const handleEvent = useCallback((event: StreamEvent) => {
    if (event.event === "briefing" || event.event === "clarification" || event.event === "error") {
      finished.current = true;
    }
    switch (event.event) {
      case "resolved":
        setRun((r) => r && { ...r, ticker: event.data.ticker, companyName: event.data.company_name });
        break;
      case "queued":
        setRun((r) => r && { ...r, queued: true });
        break;
      case "step":
        setRun((r) => r && { ...r, queued: false, steps: { ...r.steps, [event.data.step]: event.data } });
        break;
      case "briefing":
        setBriefing(event.data);
        setOrigin(event.data.cached ? "cached" : "live");
        setPhase("done");
        break;
      case "clarification":
        setMessage({ text: event.data.message });
        setPhase("clarify");
        break;
      case "error":
        setMessage({ text: event.data.message, code: event.data.code });
        setPhase("error");
        break;
    }
  }, []);

  const ask = useCallback(
    async (query: string) => {
      abort.current?.abort();
      const controller = new AbortController();
      abort.current = controller;
      setLink(null);
      setRun(emptyRun(query));
      setBriefing(null);
      setMessage(null);
      setPhase("working");
      finished.current = false;
      top.current?.scrollIntoView({ behavior: "smooth" });
      try {
        await streamBriefing(query, handleEvent, controller.signal);
        // A stream that ends without a final event (e.g. a dropped connection).
        if (!finished.current && !controller.signal.aborted) {
          setMessage({ text: "The connection was interrupted. Please try again." });
          setPhase("error");
        }
      } catch (error) {
        if (controller.signal.aborted) return;
        setMessage({ text: error instanceof Error ? error.message : "Something went wrong." });
        setPhase("error");
      } finally {
        refreshQuota();
      }
    },
    [handleEvent, refreshQuota],
  );

  const openExample = useCallback(async (ticker: string) => {
    abort.current?.abort();
    try {
      const example = await fetchExample(ticker);
      setRun(null);
      setMessage(null);
      setBriefing(example);
      setOrigin("example");
      setPhase("done");
      setLink(example.ticker);
      top.current?.scrollIntoView({ behavior: "smooth" });
    } catch {
      setMessage({ text: "That example could not be loaded." });
      setPhase("error");
    }
  }, []);

  // Shareable links: ?example=MSFT opens a saved briefing, ?q=... starts a live one.
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const example = params.get("example");
    const query = params.get("q");
    if (example) void openExample(example);
    else if (query) void ask(query);
    // Run once on first load only.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const reset = () => {
    setLink(null);
    abort.current?.abort();
    setPhase("idle");
    setRun(null);
    setBriefing(null);
    setMessage(null);
  };

  const busy = phase === "working";
  const limitReached =
    (message?.code?.endsWith("limit_reached") || message?.code === "model_unavailable") ?? false;

  return (
    <div className="flex min-h-screen flex-col">
      <Header quota={quota} onHome={reset} />
      <main ref={top} className="mx-auto w-full max-w-6xl flex-1 scroll-mt-20 px-4 pb-16 sm:px-6">
        {phase === "idle" ? (
          <section className="pt-14 text-center sm:pt-20">
            <p className="label mb-5 text-accent">Multi-agent research · live market data</p>
            <h1 className="mx-auto max-w-3xl text-balance text-4xl font-semibold tracking-tight text-white sm:text-5xl">
              Executive market briefings, researched by AI agents.
            </h1>
            <p className="mx-auto mt-5 max-w-2xl text-pretty text-base leading-relaxed text-ink-300 sm:text-lg">
              Ask about any listed company. Specialised agents analyse the financials and screen
              the week&apos;s news in parallel, then write a balanced briefing, with every figure
              and source traceable.
            </p>
          </section>
        ) : (
          <div className="flex items-center justify-between pt-6">
            <button
              onClick={reset}
              className="inline-flex items-center gap-2 rounded-md px-2 py-1 text-sm text-ink-300 transition hover:text-white"
            >
              <ArrowLeft size={16} /> New briefing
            </button>
          </div>
        )}

        <div className={phase === "idle" ? "mx-auto mt-10 max-w-2xl" : "mt-4"}>
          <Composer onSubmit={ask} disabled={busy} compact={phase !== "idle"} initial={run?.query} />
        </div>

        {busy && run && (
          <div className="mt-8 animate-fade-up">
            <AgentPipeline
              steps={run.steps}
              ticker={run.ticker}
              companyName={run.companyName}
              queued={run.queued}
              startedAt={run.startedAt}
            />
          </div>
        )}

        {(phase === "clarify" || phase === "error") && message && (
          <div className="mt-8 animate-fade-up">
            <Notice tone={phase === "clarify" ? "info" : "error"} text={message.text} />
            {limitReached && examples.length > 0 && (
              <p className="mt-3 text-sm text-ink-300">
                Meanwhile, the saved example briefings below are always available.
              </p>
            )}
          </div>
        )}

        {phase === "done" && briefing && (
          <div className="mt-8">
            <BriefingView briefing={briefing} origin={origin} />
          </div>
        )}

        {phase !== "working" && (
          <>
            {examples.length > 0 && (
              <Examples
                examples={examples}
                onOpen={openExample}
                current={origin === "example" ? briefing?.ticker : undefined}
              />
            )}
            {phase === "idle" && <HowItWorks />}
          </>
        )}
      </main>
      <Footer />
    </div>
  );
}

function Notice({ tone, text }: { tone: "info" | "error"; text: string }) {
  const Icon = tone === "info" ? HelpCircle : AlertTriangle;
  const style =
    tone === "info"
      ? "border-accent/30 bg-accent/5 text-ink-100"
      : "border-down/30 bg-down/5 text-ink-100";
  return (
    <div className={`flex items-start gap-3 rounded-xl border px-4 py-3.5 ${style}`} role="status">
      <Icon size={18} className={tone === "info" ? "mt-0.5 text-accent" : "mt-0.5 text-down"} />
      <p className="text-sm leading-relaxed">{text}</p>
    </div>
  );
}
