import { Brain, GitBranch, ShieldCheck, Workflow } from "lucide-react";

const POINTS = [
  {
    icon: Workflow,
    title: "Supervised agent graph",
    text: "A LangGraph supervisor plans each run and re-plans after every step, routing around anything that fails.",
  },
  {
    icon: GitBranch,
    title: "Parallel specialists",
    text: "The market analyst and the news analyst work at the same time; the news agent uses tools to decide what to read.",
  },
  {
    icon: ShieldCheck,
    title: "Traceable output",
    text: "Figures come from the data, not the model. News links only point to articles the agent actually cited.",
  },
  {
    icon: Brain,
    title: "Honest about gaps",
    text: "Every briefing states its confidence and missing data. Partial results are labelled, never hidden.",
  },
];

export function HowItWorks() {
  return (
    <section className="mt-16">
      <h2 className="label mb-4 text-ink-300">How it works</h2>
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4">
        {POINTS.map(({ icon: Icon, title, text }) => (
          <div key={title} className="panel p-5">
            <Icon size={18} className="text-accent" />
            <h3 className="mt-3 text-sm font-semibold text-white">{title}</h3>
            <p className="mt-1.5 text-sm leading-relaxed text-ink-400">{text}</p>
          </div>
        ))}
      </div>
    </section>
  );
}
