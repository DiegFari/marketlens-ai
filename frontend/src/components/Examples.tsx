import { ArrowUpRight } from "lucide-react";

import { shortDate } from "../format";
import type { ExampleSummary } from "../types";

interface Props {
  examples: ExampleSummary[];
  onOpen: (ticker: string) => void;
  current?: string;
}

export function Examples({ examples, onOpen, current }: Props) {
  return (
    <section className="mt-16">
      <div className="mb-4 flex items-end justify-between gap-4">
        <div>
          <h2 className="label text-ink-300">Example briefings</h2>
          <p className="mt-1 text-sm text-ink-400">
            Real outputs saved from earlier runs. Open one instantly, no waiting.
          </p>
        </div>
      </div>
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-5">
        {examples.map((example) => (
          <button
            key={example.ticker}
            onClick={() => onOpen(example.ticker)}
            className={`panel group flex flex-col items-start p-4 text-left transition hover:-translate-y-0.5 hover:border-accent/50 ${
              current === example.ticker ? "border-accent/60" : ""
            }`}
          >
            <div className="flex w-full items-center justify-between">
              <span className="font-mono text-sm font-semibold text-accent">{example.ticker}</span>
              <ArrowUpRight
                size={15}
                className="text-ink-500 transition group-hover:text-accent"
              />
            </div>
            <span className="mt-2 line-clamp-1 text-sm font-medium text-ink-100">
              {example.company_name ?? example.ticker}
            </span>
            <span className="mt-0.5 text-xs text-ink-400">{shortDate(example.generated_at)}</span>
          </button>
        ))}
      </div>
    </section>
  );
}
