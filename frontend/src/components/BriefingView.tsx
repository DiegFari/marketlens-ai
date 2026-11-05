import {
  AlertTriangle,
  ArrowDownRight,
  ArrowUpRight,
  ChevronDown,
  Clock,
  ExternalLink,
  ShieldCheck,
  TrendingDown,
  TrendingUp,
} from "lucide-react";
import type { ReactNode } from "react";

import type { Origin } from "../App";
import {
  cleanEvidence,
  compactMoney,
  dateTime,
  money,
  pct,
  pctPoints,
  rangePosition,
  ratio,
  seconds,
  shortDate,
  vsAverage,
} from "../format";
import type { Briefing, Development, Level, Sentiment, Snapshot, Source } from "../types";
import { STEPS } from "./AgentPipeline";
import { Markdown } from "./Markdown";

export function BriefingView({ briefing, origin }: { briefing: Briefing; origin: Origin }) {
  const { snapshot, briefing: report, market_analysis: market, news_analysis: news } = briefing;
  const name = briefing.company_name ?? snapshot?.profile.name ?? briefing.ticker;
  const sources = new Map(briefing.sources.map((s) => [s.id, s]));

  return (
    <article className="animate-fade-up space-y-5">
      <TitleBar briefing={briefing} name={name} origin={origin} />

      {briefing.warnings.length > 0 && (
        <div className="space-y-2">
          {briefing.warnings.map((w) => (
            <div
              key={w.code}
              className="flex items-start gap-3 rounded-lg border border-warn/30 bg-warn/5 px-4 py-3 text-sm text-ink-100"
            >
              <AlertTriangle size={16} className="mt-0.5 shrink-0 text-warn" />
              {w.message}
            </div>
          ))}
        </div>
      )}

      {snapshot && <MarketStrip snapshot={snapshot} />}

      <div className="grid grid-cols-1 gap-5 lg:grid-cols-3 [&>*]:min-w-0">
        <div className="space-y-5 lg:col-span-2">
          {report && (
            <Card title="Company summary">
              <Prose>{report.company_summary}</Prose>
              {snapshot && <ProfileLine snapshot={snapshot} />}
            </Card>
          )}
          {(report || market) && (
            <Card title="Market performance">
              {report && <Prose>{report.market_performance}</Prose>}
              {market && (
                <p className="mt-3 border-l-2 border-accent/50 pl-3 text-sm leading-relaxed text-ink-200">
                  <span className="font-medium text-white">Valuation. </span>
                  {market.valuation_view}
                </p>
              )}
            </Card>
          )}
          {news && news.developments.length > 0 && (
            <Card
              title="Key developments"
              aside={<SentimentPill sentiment={news.overall_sentiment} label="overall" />}
            >
              <Prose>{news.summary}</Prose>
              <ul className="mt-4 space-y-3">
                {news.developments.map((dev, i) => (
                  <DevelopmentItem key={i} dev={dev} sources={sources} />
                ))}
              </ul>
            </Card>
          )}
        </div>

        <div className="space-y-5">
          {snapshot?.metrics && <MetricsCard snapshot={snapshot} />}
          {market && (market.strengths.length > 0 || market.concerns.length > 0) && (
            <Card title="Financial signals">
              <SignalList items={market.strengths} positive />
              <SignalList items={market.concerns} positive={false} />
            </Card>
          )}
        </div>
      </div>

      {report && (
        <div className="grid grid-cols-1 gap-5 md:grid-cols-2 [&>*]:min-w-0">
          <BulletCard title="Bullish signals" items={report.bullish_signals} positive />
          <BulletCard title="Risk factors" items={report.risk_factors} positive={false} />
        </div>
      )}

      {report && (
        <section className="panel relative overflow-hidden p-6">
          <div className="absolute inset-y-0 left-0 w-1 bg-gradient-to-b from-accent to-up" />
          <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
            <h3 className="label text-ink-300">Overall assessment</h3>
            <ConfidenceBadge level={report.confidence} />
          </div>
          <p className="text-[15px] leading-relaxed text-ink-100 sm:text-base">
            {report.overall_assessment}
          </p>
          <p className="mt-4 text-sm leading-relaxed text-ink-400">{report.confidence_note}</p>
        </section>
      )}

      {!report && <FallbackMarkdown markdown={briefing.report_markdown} />}

      <Trace briefing={briefing} />

      <p className="text-xs leading-relaxed text-ink-400">
        Sources: market data from Financial Modeling Prep; news from NewsAPI
        {news ? `; ${briefing.sources.length} articles cited` : ""}. Figures come from the data, not
        the model. Generated automatically for research purposes. Not investment advice.
      </p>
    </article>
  );
}

// ------------------------------------------------------------------ header

function TitleBar({ briefing, name, origin }: { briefing: Briefing; name: string; origin: Origin }) {
  return (
    <header className="flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
      <div>
        <div className="mb-2 flex flex-wrap items-center gap-2">
          <span className="rounded-md border border-accent/40 bg-accent/10 px-2 py-0.5 font-mono text-sm font-semibold text-accent">
            {briefing.ticker}
          </span>
          <StatusBadge status={briefing.status} />
          <OriginBadge origin={origin} />
        </div>
        <h2 className="text-2xl font-semibold tracking-tight text-white sm:text-3xl">{name}</h2>
      </div>
      <p className="flex items-center gap-1.5 text-xs text-ink-400">
        <Clock size={13} /> {dateTime(briefing.generated_at)}
      </p>
    </header>
  );
}

function StatusBadge({ status }: { status: Briefing["status"] }) {
  return status === "complete" ? (
    <span className="inline-flex items-center gap-1 rounded-md border border-up/30 bg-up/10 px-2 py-0.5 text-xs font-medium text-up">
      <ShieldCheck size={12} /> Complete
    </span>
  ) : (
    <span className="inline-flex items-center gap-1 rounded-md border border-warn/30 bg-warn/10 px-2 py-0.5 text-xs font-medium text-warn">
      <AlertTriangle size={12} /> Partial
    </span>
  );
}

function OriginBadge({ origin }: { origin: Origin }) {
  const text = { live: "Live run", cached: "Recent run", example: "Saved example" }[origin];
  return (
    <span className="rounded-md border border-ink-700 px-2 py-0.5 text-xs text-ink-300">{text}</span>
  );
}

// ------------------------------------------------------------------ market data

function MarketStrip({ snapshot }: { snapshot: Snapshot }) {
  const { quote, profile } = snapshot;
  const currency = profile.currency ?? "USD";
  const change = quote.change_percent;
  const up = (change ?? 0) >= 0;
  const position = rangePosition(quote.price, quote.year_low, quote.year_high);
  const vs50 = vsAverage(quote.price, quote.avg_price_50d);
  const vs200 = vsAverage(quote.price, quote.avg_price_200d);

  return (
    <section className="panel grid grid-cols-1 gap-px overflow-hidden bg-ink-700/40 sm:grid-cols-2 lg:grid-cols-4 [&>*]:min-w-0">
      <Cell label="Share price">
        <div className="flex items-baseline gap-3">
          <span className="num text-2xl font-semibold text-white">{money(quote.price, currency)}</span>
          {change != null && (
            <span className={`num inline-flex items-center text-sm font-medium ${up ? "text-up" : "text-down"}`}>
              {up ? <ArrowUpRight size={15} /> : <ArrowDownRight size={15} />}
              {pctPoints(change)}
            </span>
          )}
        </div>
        <p className="mt-1 text-xs text-ink-400">
          {quote.exchange ?? profile.exchange}
          {quote.as_of && <> · as of {shortDate(quote.as_of)}</>}
        </p>
      </Cell>
      <Cell label="Market cap">
        <span className="num text-2xl font-semibold text-white">
          {compactMoney(quote.market_cap, currency)}
        </span>
      </Cell>
      <Cell label="52-week range">
        {position != null ? (
          <>
            <div className="relative mt-2 h-1.5 rounded-full bg-gradient-to-r from-down/50 via-ink-500 to-up/60">
              <div
                className="absolute top-1/2 h-3.5 w-1 -translate-x-1/2 -translate-y-1/2 rounded-sm bg-white shadow"
                style={{ left: `${position * 100}%` }}
              />
            </div>
            <div className="num mt-2 flex justify-between text-xs text-ink-400">
              <span>{money(quote.year_low, currency)}</span>
              <span className="text-ink-200">{Math.round(position * 100)}%</span>
              <span>{money(quote.year_high, currency)}</span>
            </div>
          </>
        ) : (
          <span className="text-ink-400">—</span>
        )}
      </Cell>
      <Cell label="Trend vs averages">
        <div className="num space-y-1 text-sm">
          <TrendLine label="50-day" value={vs50} />
          <TrendLine label="200-day" value={vs200} />
        </div>
      </Cell>
    </section>
  );
}

function TrendLine({ label, value }: { label: string; value: number | null }) {
  const up = (value ?? 0) >= 0;
  return (
    <div className="flex items-center justify-between">
      <span className="text-ink-400">{label}</span>
      <span className={value == null ? "text-ink-400" : up ? "text-up" : "text-down"}>
        {pct(value, { signed: true })}
      </span>
    </div>
  );
}

function Cell({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="bg-ink-900 px-5 py-4">
      <p className="label mb-2">{label}</p>
      {children}
    </div>
  );
}

function MetricsCard({ snapshot }: { snapshot: Snapshot }) {
  const m = snapshot.metrics!;
  const rows: [string, string, boolean | null][] = [
    [
      `Revenue growth${m.revenue_growth_period ? ` (${m.revenue_growth_period})` : ""}`,
      pct(m.revenue_growth, { signed: true }),
      m.revenue_growth == null ? null : m.revenue_growth >= 0,
    ],
    ["P/E (TTM)", ratio(m.pe_ratio), null],
    ["Price / book", ratio(m.price_to_book), null],
    ["Gross margin", pct(m.gross_margin), null],
    ["Operating margin", pct(m.operating_margin), null],
    ["Net margin", pct(m.net_margin), m.net_margin == null ? null : m.net_margin >= 0],
    ["Return on equity", pct(m.return_on_equity), null],
    ["Debt / equity", ratio(m.debt_to_equity, ""), null],
    ["FCF yield", pct(m.free_cash_flow_yield), null],
    ["Dividend yield", pct(m.dividend_yield), null],
  ];
  return (
    <Card title="Key metrics">
      <dl className="divide-y divide-ink-800">
        {rows
          .filter(([, value]) => value !== "—")
          .map(([label, value, good]) => (
            <div key={label} className="flex items-center justify-between py-2 text-sm">
              <dt className="text-ink-300">{label}</dt>
              <dd
                className={`num font-medium ${
                  good == null ? "text-ink-100" : good ? "text-up" : "text-down"
                }`}
              >
                {value}
              </dd>
            </div>
          ))}
      </dl>
    </Card>
  );
}

function ProfileLine({ snapshot }: { snapshot: Snapshot }) {
  const p = snapshot.profile;
  const parts = [
    p.sector,
    p.industry,
    p.country,
    p.employees ? `${p.employees.toLocaleString("en-US")} employees` : null,
    p.ceo ? `CEO ${p.ceo}` : null,
  ].filter(Boolean);
  if (!parts.length) return null;
  return (
    <div className="mt-4 flex flex-wrap gap-1.5">
      {parts.map((part) => (
        <span key={part} className="rounded-md bg-ink-800 px-2 py-1 text-xs text-ink-300">
          {part}
        </span>
      ))}
    </div>
  );
}

// ------------------------------------------------------------------ analysis

function DevelopmentItem({ dev, sources }: { dev: Development; sources: Map<number, Source> }) {
  const cited = dev.article_ids.map((id) => sources.get(id)).filter((s): s is Source => !!s);
  return (
    <li className="rounded-lg border border-ink-700/70 bg-ink-850/60 p-4">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <h4 className="text-sm font-semibold text-white">{dev.headline}</h4>
        <div className="flex shrink-0 items-center gap-1.5">
          <ImportanceDots level={dev.importance} />
          <SentimentPill sentiment={dev.sentiment} />
        </div>
      </div>
      <p className="mt-1.5 text-sm leading-relaxed text-ink-300">{dev.summary}</p>
      {cited.length > 0 && (
        <div className="mt-3 flex flex-wrap gap-x-4 gap-y-1">
          {cited.slice(0, 3).map((s) => (
            <a
              key={s.id}
              href={s.url}
              target="_blank"
              rel="noreferrer noopener"
              title={s.title}
              className="inline-flex items-center gap-1 text-xs text-accent/90 hover:text-accent hover:underline"
            >
              {s.source ?? "Source"} <ExternalLink size={11} />
            </a>
          ))}
        </div>
      )}
    </li>
  );
}

const SENTIMENT: Record<Sentiment, string> = {
  positive: "border-up/30 bg-up/10 text-up",
  negative: "border-down/30 bg-down/10 text-down",
  mixed: "border-warn/30 bg-warn/10 text-warn",
  neutral: "border-ink-600 bg-ink-800 text-ink-300",
};

function SentimentPill({ sentiment, label }: { sentiment: Sentiment; label?: string }) {
  return (
    <span className={`rounded-md border px-1.5 py-0.5 font-mono text-[10px] uppercase tracking-wider ${SENTIMENT[sentiment]}`}>
      {label ? `${label} · ` : ""}
      {sentiment}
    </span>
  );
}

function ImportanceDots({ level }: { level: Level }) {
  const filled = { high: 3, medium: 2, low: 1 }[level];
  return (
    <span className="flex gap-0.5" title={`${level} importance`} aria-label={`${level} importance`}>
      {[0, 1, 2].map((i) => (
        <span key={i} className={`h-1.5 w-1.5 rounded-full ${i < filled ? "bg-accent" : "bg-ink-700"}`} />
      ))}
    </span>
  );
}

function SignalList({ items, positive }: { items: { title: string; evidence: string }[]; positive: boolean }) {
  if (!items.length) return null;
  return (
    <div className="mb-4 last:mb-0">
      <p className={`label mb-2 ${positive ? "text-up/80" : "text-down/80"}`}>
        {positive ? "Strengths" : "Concerns"}
      </p>
      <ul className="space-y-2.5">
        {items.map((item) => (
          <li key={item.title} className="text-sm">
            <p className="font-medium text-ink-100">{item.title}</p>
            <p className="num text-xs text-ink-400">{cleanEvidence(item.evidence)}</p>
          </li>
        ))}
      </ul>
    </div>
  );
}

function BulletCard({ title, items, positive }: { title: string; items: string[]; positive: boolean }) {
  const Icon = positive ? TrendingUp : TrendingDown;
  return (
    <section className={`panel p-5 ${positive ? "border-up/20" : "border-down/20"}`}>
      <h3 className={`mb-3 flex items-center gap-2 text-sm font-semibold ${positive ? "text-up" : "text-down"}`}>
        <Icon size={16} /> {title}
      </h3>
      <ul className="space-y-2">
        {items.map((item) => (
          <li key={item} className="flex gap-2.5 text-sm leading-relaxed text-ink-200">
            <span className={`mt-2 h-1 w-1 shrink-0 rounded-full ${positive ? "bg-up" : "bg-down"}`} />
            {item}
          </li>
        ))}
      </ul>
    </section>
  );
}

function ConfidenceBadge({ level }: { level: Level }) {
  const style = {
    high: "border-up/30 text-up",
    medium: "border-warn/30 text-warn",
    low: "border-down/30 text-down",
  }[level];
  return (
    <span className={`rounded-md border px-2 py-0.5 font-mono text-[11px] uppercase tracking-wider ${style}`}>
      Confidence · {level}
    </span>
  );
}

// ------------------------------------------------------------------ trace

function Trace({ briefing }: { briefing: Briefing }) {
  const steps = briefing.trace.filter((t) => t.step !== "supervisor");
  if (!steps.length) return null;
  const labels = Object.fromEntries(STEPS.map((s) => [s.key, s.label]));
  const total = steps.reduce((sum, t) => sum + t.duration_ms, 0);
  const tokens = steps.reduce((sum, t) => sum + t.input_tokens + t.output_tokens, 0);
  const calls = steps.reduce((sum, t) => sum + t.llm_calls, 0);
  const max = Math.max(...steps.map((t) => t.duration_ms), 1);

  return (
    <details className="panel group">
      <summary className="flex cursor-pointer list-none items-center justify-between px-5 py-4">
        <span className="text-sm font-medium text-ink-100">How this briefing was produced</span>
        <span className="flex items-center gap-3 text-xs text-ink-400">
          <span className="num hidden sm:inline">
            {calls} model calls · {tokens.toLocaleString("en-US")} tokens · {briefing.model}
          </span>
          <ChevronDown size={16} className="transition group-open:rotate-180" />
        </span>
      </summary>
      <div className="border-t border-ink-700/70 px-5 py-4">
        <ul className="space-y-2.5">
          {steps.map((t, i) => (
            <li key={i} className="grid grid-cols-[7rem_minmax(0,1fr)_4rem] sm:grid-cols-[8.5rem_minmax(0,1fr)_4.5rem] items-center gap-3 text-xs">
              <span className="truncate text-ink-200">{labels[t.step] ?? t.step}</span>
              <div className="h-2 overflow-hidden rounded-full bg-ink-800">
                <div
                  className={`h-full rounded-full ${t.status === "failed" ? "bg-down" : "bg-accent/70"}`}
                  style={{ width: `${Math.max((t.duration_ms / max) * 100, 2)}%` }}
                />
              </div>
              <span className="num text-right text-ink-400">{seconds(t.duration_ms)}</span>
            </li>
          ))}
        </ul>
        <p className="num mt-4 text-xs text-ink-400">
          Agent time {seconds(total)} (market and news agents run in parallel). Routing is a
          deterministic supervisor; figures and links are rendered from data.
        </p>
      </div>
    </details>
  );
}

// ------------------------------------------------------------------ shared

function Card({ title, aside, children }: { title: string; aside?: ReactNode; children: ReactNode }) {
  return (
    <section className="panel p-5">
      <div className="mb-3 flex items-center justify-between gap-2">
        <h3 className="label text-ink-300">{title}</h3>
        {aside}
      </div>
      {children}
    </section>
  );
}

function Prose({ children }: { children: ReactNode }) {
  return <p className="text-[15px] leading-relaxed text-ink-200">{children}</p>;
}

function FallbackMarkdown({ markdown }: { markdown: string }) {
  return (
    <Card title="Briefing">
      <Markdown source={markdown} />
    </Card>
  );
}
