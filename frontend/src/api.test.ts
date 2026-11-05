import { describe, expect, it } from "vitest";

import { parseSSE } from "./api";
import { cleanEvidence, pct, pctPoints, rangePosition, ratio } from "./format";

describe("parseSSE", () => {
  it("parses complete events and keeps the unfinished rest", () => {
    const buffer =
      'event: resolved\ndata: {"ticker":"AMZN","company_name":null,"cached":false}\n\n' +
      ": keep-alive\n\n" +
      'event: step\ndata: {"step":"collect';
    const { events, rest } = parseSSE(buffer);

    expect(events).toEqual([
      { event: "resolved", data: { ticker: "AMZN", company_name: null, cached: false } },
    ]);
    expect(rest).toBe('event: step\ndata: {"step":"collect');
  });

  it("skips malformed events", () => {
    const { events } = parseSSE("event: step\ndata: {broken\n\nevent: queued\ndata: {}\n\n");
    expect(events).toEqual([{ event: "queued", data: {} }]);
  });
});

describe("format", () => {
  it("formats ratios and percentages", () => {
    expect(pct(0.178, { signed: true })).toBe("+17.8%");
    expect(pct(null)).toBe("—");
    expect(pctPoints(-1.234)).toBe("-1.23%");
    expect(ratio(29.12)).toBe("29.1x");
  });

  it("tidies raw evidence strings", () => {
    expect(cleanEvidence("return_on_equity: 0.3320594736860485 (33.2%)")).toBe(
      "Return on equity: 33.2%",
    );
    expect(cleanEvidence("pe_ratio: 28.20520833333333")).toBe("P/E ratio: 28.21");
    expect(cleanEvidence("ev_to_ebitda: 7.69")).toBe("EV to EBITDA: 7.69");
    expect(cleanEvidence("Net margin 40.3%")).toBe("Net margin 40.3%");
  });

  it("places the price in its 52-week range", () => {
    expect(rangePosition(150, 100, 200)).toBe(0.5);
    expect(rangePosition(250, 100, 200)).toBe(1);
    expect(rangePosition(150, null, 200)).toBeNull();
  });
});
