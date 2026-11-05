// Number and date formatting for the briefing view. Ratios arrive as fractions (0.403).

const dash = "—";

export function money(value: number | null | undefined, currency = "USD"): string {
  if (value == null) return dash;
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency,
    maximumFractionDigits: 2,
  }).format(value);
}

export function compactMoney(value: number | null | undefined, currency = "USD"): string {
  if (value == null) return dash;
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency,
    notation: "compact",
    maximumFractionDigits: 2,
  }).format(value);
}

/** A fraction (0.178) as a percentage ("17.8%"); ``signed`` adds "+" for gains. */
export function pct(value: number | null | undefined, { signed = false } = {}): string {
  if (value == null) return dash;
  const text = `${(value * 100).toFixed(1)}%`;
  return signed && value > 0 ? `+${text}` : text;
}

/** A value already in percent (1.25 = 1.25%), as the quote's change_percent is. */
export function pctPoints(value: number | null | undefined): string {
  if (value == null) return dash;
  return `${value > 0 ? "+" : ""}${value.toFixed(2)}%`;
}

export function ratio(value: number | null | undefined, suffix = "x"): string {
  if (value == null) return dash;
  return `${value.toFixed(value >= 100 ? 0 : 1)}${suffix}`;
}

export function seconds(ms: number | null | undefined): string {
  if (ms == null) return "";
  return ms < 1000 ? `${Math.round(ms)} ms` : `${(ms / 1000).toFixed(1)} s`;
}

export function dateTime(iso: string | null | undefined): string {
  if (!iso) return dash;
  return new Intl.DateTimeFormat("en-GB", {
    day: "numeric",
    month: "short",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    timeZone: "UTC",
    timeZoneName: "short",
  }).format(new Date(iso));
}

export function shortDate(iso: string | null | undefined): string {
  if (!iso) return dash;
  return new Intl.DateTimeFormat("en-GB", { day: "numeric", month: "short" }).format(
    new Date(iso),
  );
}

/**
 * Tidy the model's evidence strings, which sometimes quote raw fields:
 * "return_on_equity: 0.3320594736860485 (33.2%)" -> "Return on equity: 33.2%".
 */
export function cleanEvidence(text: string): string {
  return text.replace(
    /\b([a-z]+(?:_[a-z]+)+|[a-z]{3,}_?[a-z]*):\s*(-?\d+(?:\.\d+)?)(?:\s*\(([^)]+)\))?/g,
    (_, field: string, number: string, readable?: string) => {
      const label = field
        .replace(/_/g, " ")
        .replace(/^./, (c) => c.toUpperCase())
        .replace(/^Pe\b/, "P/E")
        .replace(/\b(ev|ebitda|fcf|roe|roa)\b/gi, (w) => w.toUpperCase());
      const value = readable ?? String(Math.round(Number(number) * 100) / 100);
      return `${label}: ${value}`;
    },
  );
}

/** Where the price sits in its 52-week range, 0..1 (null when unknown). */
export function rangePosition(price: number, low: number | null, high: number | null) {
  if (low == null || high == null || high <= low) return null;
  return Math.min(Math.max((price - low) / (high - low), 0), 1);
}

export function vsAverage(price: number, average: number | null): number | null {
  return average ? price / average - 1 : null;
}
