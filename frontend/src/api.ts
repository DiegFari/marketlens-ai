import type { Briefing, ExampleSummary, Quota, StreamEvent } from "./types";

const API = "/api/v1";

/** Split a Server-Sent Events buffer into complete events plus the unfinished rest. */
export function parseSSE(buffer: string): { events: StreamEvent[]; rest: string } {
  const blocks = buffer.split(/\r?\n\r?\n/);
  const rest = blocks.pop() ?? "";
  const events: StreamEvent[] = [];
  for (const block of blocks) {
    let event = "";
    const data: string[] = [];
    for (const line of block.split(/\r?\n/)) {
      if (line.startsWith(":")) continue; // keep-alive comment
      if (line.startsWith("event:")) event = line.slice(6).trim();
      else if (line.startsWith("data:")) data.push(line.slice(5).trimStart());
    }
    if (event && data.length) {
      try {
        events.push({ event, data: JSON.parse(data.join("\n")) } as StreamEvent);
      } catch {
        // A malformed event is skipped rather than breaking the whole stream.
      }
    }
  }
  return { events, rest };
}

/** POST the request and call ``onEvent`` for each streamed event until the stream ends. */
export async function streamBriefing(
  message: string,
  onEvent: (event: StreamEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  const response = await fetch(`${API}/briefings/stream`, {
    method: "POST",
    headers: { "content-type": "application/json", accept: "text/event-stream" },
    body: JSON.stringify({ message }),
    signal,
  });
  if (!response.ok || !response.body) {
    const body = await response.json().catch(() => null);
    throw new Error(body?.error?.message ?? `Request failed (${response.status})`);
  }
  const reader = response.body.pipeThrough(new TextDecoderStream()).getReader();
  let buffer = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    const parsed = parseSSE(buffer + value);
    buffer = parsed.rest;
    parsed.events.forEach(onEvent);
  }
  if (buffer.trim()) parseSSE(buffer + "\n\n").events.forEach(onEvent);
}

async function getJSON<T>(path: string): Promise<T> {
  const response = await fetch(`${API}${path}`);
  if (!response.ok) throw new Error(`Request failed (${response.status})`);
  return (await response.json()) as T;
}

export const fetchExamples = () => getJSON<ExampleSummary[]>("/examples");
export const fetchExample = (ticker: string) =>
  getJSON<Briefing>(`/examples/${encodeURIComponent(ticker)}`);
export const fetchQuota = () => getJSON<Quota>("/briefings/quota");
