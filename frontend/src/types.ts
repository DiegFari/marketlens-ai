// Mirrors the API's response models (src/marketlens/api/schemas/analysis.py).

export type StepName =
  | "collect_data"
  | "market_analyst"
  | "news_analyst"
  | "report_writer"
  | "finalize";

export type StepStatus = "running" | "succeeded" | "failed" | "skipped";
export type Sentiment = "positive" | "negative" | "neutral" | "mixed";
export type Level = "high" | "medium" | "low";

export interface StepEvent {
  step: StepName | string;
  status: StepStatus;
  duration_ms: number | null;
  detail: string | null;
  llm_calls: number;
  tool_calls: string[];
}

export interface TraceEvent {
  step: string;
  status: Exclude<StepStatus, "running">;
  started_at: string;
  duration_ms: number;
  detail: string | null;
  llm_calls: number;
  input_tokens: number;
  output_tokens: number;
  tool_calls: string[];
}

export interface Quote {
  price: number;
  change: number | null;
  change_percent: number | null;
  year_low: number | null;
  year_high: number | null;
  market_cap: number | null;
  avg_price_50d: number | null;
  avg_price_200d: number | null;
  exchange: string | null;
  as_of: string | null;
}

export interface Profile {
  name: string;
  description: string | null;
  sector: string | null;
  industry: string | null;
  country: string | null;
  exchange: string | null;
  currency: string | null;
  ceo: string | null;
  employees: number | null;
}

export interface Metrics {
  pe_ratio: number | null;
  price_to_book: number | null;
  debt_to_equity: number | null;
  return_on_equity: number | null;
  gross_margin: number | null;
  operating_margin: number | null;
  net_margin: number | null;
  dividend_yield: number | null;
  free_cash_flow_yield: number | null;
  revenue_growth: number | null;
  revenue_growth_period: string | null;
}

export interface Snapshot {
  symbol: string;
  quote: Quote;
  profile: Profile;
  metrics: Metrics | null;
}

export interface Signal {
  title: string;
  evidence: string;
}

export interface MarketAnalysis {
  fundamentals_summary: string;
  strengths: Signal[];
  concerns: Signal[];
  valuation_view: string;
  confidence: Level;
  data_gaps: string[];
}

export interface Development {
  headline: string;
  summary: string;
  sentiment: Sentiment;
  importance: Level;
  article_ids: number[];
}

export interface NewsAnalysis {
  summary: string;
  developments: Development[];
  overall_sentiment: Sentiment;
  sentiment_confidence: Level;
  coverage_note: string;
}

export interface ExecutiveBriefing {
  company_summary: string;
  market_performance: string;
  bullish_signals: string[];
  risk_factors: string[];
  overall_assessment: string;
  confidence: Level;
  confidence_note: string;
}

export interface Warning {
  source: string;
  code: string;
  message: string;
  severity: "info" | "warning";
}

export interface Source {
  id: number;
  title: string;
  url: string;
  source: string | null;
  published_at: string | null;
}

export interface Briefing {
  ticker: string;
  company_name: string | null;
  status: "complete" | "partial";
  generated_at: string;
  report_markdown: string;
  warnings: Warning[];
  briefing: ExecutiveBriefing | null;
  market_analysis: MarketAnalysis | null;
  news_analysis: NewsAnalysis | null;
  snapshot: Snapshot | null;
  trace: TraceEvent[];
  model: string;
  sources: Source[];
  cached: boolean;
}

export interface ExampleSummary {
  ticker: string;
  company_name: string | null;
  generated_at: string;
}

export interface Quota {
  limits_enabled: boolean;
  remaining_today: number | null;
}

export type StreamEvent =
  | { event: "resolved"; data: { ticker: string; company_name: string | null; cached: boolean } }
  | { event: "queued"; data: { message: string } }
  | { event: "step"; data: StepEvent }
  | { event: "briefing"; data: Briefing }
  | { event: "clarification"; data: { message: string } }
  | { event: "error"; data: { code: string; message: string } };
