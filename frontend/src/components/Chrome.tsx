import { Github } from "lucide-react";

import type { Quota } from "../types";

const REPO = "https://github.com/DiegFari/marketlens-ai";

export function Logo() {
  return (
    <svg viewBox="0 0 32 32" className="h-7 w-7" aria-hidden>
      <rect width="32" height="32" rx="7" className="fill-ink-800" />
      <path d="M16 5 27 16 16 27 5 16Z" fill="none" stroke="#38bdf8" strokeWidth="2.2" />
      <path
        d="M10 19l4-4 3 3 5-6"
        fill="none"
        stroke="#22c55e"
        strokeWidth="2"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

export function Header({ quota, onHome }: { quota: Quota | null; onHome: () => void }) {
  const remaining = quota?.remaining_today;
  return (
    <header className="sticky top-0 z-20 border-b border-ink-800/80 bg-ink-950/80 backdrop-blur-md">
      <div className="mx-auto flex h-14 max-w-6xl items-center justify-between px-4 sm:px-6">
        <button onClick={onHome} className="flex items-center gap-2.5" aria-label="MarketLens home">
          <Logo />
          <span className="text-[15px] font-semibold tracking-tight text-white">
            MarketLens <span className="text-accent">AI</span>
          </span>
        </button>
        <div className="flex items-center gap-3 sm:gap-5">
          <div className="hidden items-center gap-2 sm:flex" title="Live demo status">
            <span className="relative flex h-2 w-2">
              <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-up/60" />
              <span className="relative inline-flex h-2 w-2 rounded-full bg-up" />
            </span>
            <span className="label text-ink-300">
              Live{remaining != null && <> · {remaining} briefings left today</>}
            </span>
          </div>
          <a
            href={REPO}
            target="_blank"
            rel="noreferrer"
            className="inline-flex items-center gap-2 rounded-md border border-ink-700 px-2.5 py-1.5 text-xs font-medium text-ink-200 transition hover:border-ink-500 hover:text-white"
          >
            <Github size={14} /> <span className="hidden sm:inline">Source</span>
          </a>
        </div>
      </div>
    </header>
  );
}

export function Footer() {
  return (
    <footer className="border-t border-ink-800/80">
      <div className="mx-auto flex max-w-6xl flex-col gap-2 px-4 py-6 text-xs text-ink-400 sm:flex-row sm:items-center sm:justify-between sm:px-6">
        <p>
          Research briefings generated from third-party data. Not investment advice and not a
          price prediction.
        </p>
        <p className="font-mono">
          Python · LangGraph · FastAPI · React ·{" "}
          <a href={REPO} className="text-ink-300 underline-offset-2 hover:underline">
            GitHub
          </a>
        </p>
      </div>
    </footer>
  );
}
