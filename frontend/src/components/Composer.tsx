import { ArrowRight, Loader2, Search } from "lucide-react";
import { useEffect, useState, type FormEvent } from "react";

const SUGGESTIONS = [
  "Brief me on AMZN",
  "What's going on with Nvidia?",
  "How is Microsoft doing?",
  "TSLA",
];

interface Props {
  onSubmit: (query: string) => void;
  disabled: boolean;
  compact: boolean;
  initial?: string;
}

export function Composer({ onSubmit, disabled, compact, initial }: Props) {
  const [value, setValue] = useState(initial ?? "");

  useEffect(() => {
    if (initial !== undefined) setValue(initial);
  }, [initial]);

  const submit = (event?: FormEvent) => {
    event?.preventDefault();
    const query = value.trim();
    if (query && !disabled) onSubmit(query);
  };

  return (
    <div>
      <form
        onSubmit={submit}
        className="group relative flex items-center rounded-xl border border-ink-600/80 bg-ink-900/90 shadow-[0_0_0_1px_rgb(0_0_0/0.4),0_20px_50px_-20px_rgb(56_189_248/0.25)] transition focus-within:border-accent/60"
      >
        <Search size={18} className="pointer-events-none ml-4 shrink-0 text-ink-400" />
        <label htmlFor="query" className="sr-only">
          Ask for a briefing
        </label>
        <input
          id="query"
          value={value}
          onChange={(e) => setValue(e.target.value)}
          maxLength={300}
          autoComplete="off"
          autoFocus={!compact}
          placeholder="Ask about any listed company, e.g. “brief me on Amazon”"
          className={`w-full min-w-0 bg-transparent px-3 text-ink-100 placeholder:text-ink-400 focus:outline-none focus-visible:ring-0 focus-visible:ring-offset-0 ${
            compact ? "py-3 text-[15px]" : "py-4 text-base sm:text-lg"
          }`}
        />
        <button
          type="submit"
          disabled={disabled || !value.trim()}
          className="mr-2 inline-flex shrink-0 items-center gap-2 rounded-lg bg-accent px-3.5 py-2 text-sm font-semibold text-ink-950 transition hover:bg-sky-300 disabled:cursor-not-allowed disabled:bg-ink-700 disabled:text-ink-400"
        >
          {disabled ? <Loader2 size={16} className="animate-spin" /> : <ArrowRight size={16} />}
          <span className="hidden sm:inline">{disabled ? "Researching" : "Brief me"}</span>
        </button>
      </form>
      {!compact && (
        <div className="mt-4 flex flex-wrap justify-center gap-2">
          {SUGGESTIONS.map((suggestion) => (
            <button
              key={suggestion}
              disabled={disabled}
              onClick={() => {
                setValue(suggestion);
                onSubmit(suggestion);
              }}
              className="rounded-full border border-ink-700 bg-ink-900/60 px-3 py-1.5 text-xs text-ink-300 transition hover:border-accent/50 hover:text-white"
            >
              {suggestion}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
