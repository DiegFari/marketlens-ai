import type { ReactNode } from "react";

/**
 * Minimal Markdown for the API's fallback briefing: headings, paragraphs, bullet lists,
 * block quotes, tables, **bold**, *italic* and [links](https://...). Builds React
 * elements only (never raw HTML), so model or provider text cannot inject markup.
 */
export function Markdown({ source }: { source: string }) {
  const blocks: ReactNode[] = [];
  const lines = source.split("\n");
  let i = 0;
  while (i < lines.length) {
    const line = lines[i];
    if (!line.trim() || /^-{3,}$/.test(line.trim())) {
      i++;
      continue;
    }
    const heading = /^(#{1,4})\s+(.*)$/.exec(line);
    if (heading) {
      const level = heading[1].length;
      const cls =
        level === 1
          ? "text-xl font-semibold text-white"
          : level === 2
            ? "label mt-6 text-ink-300"
            : "text-sm font-semibold text-white mt-4";
      blocks.push(
        <p key={i} className={cls}>
          {inline(heading[2])}
        </p>,
      );
      i++;
      continue;
    }
    const group = (test: (l: string) => boolean) => {
      const items: string[] = [];
      while (i < lines.length && test(lines[i])) items.push(lines[i++]);
      return items;
    };
    if (line.startsWith(">")) {
      const items = group((l) => l.startsWith(">")).map((l) => l.replace(/^>\s?/, ""));
      blocks.push(
        <blockquote key={i} className="border-l-2 border-warn/50 pl-3 text-sm text-ink-300">
          <Markdown source={items.join("\n")} />
        </blockquote>,
      );
      continue;
    }
    if (/^\s*[-*]\s+/.test(line)) {
      const items = group((l) => /^\s*[-*]\s+/.test(l));
      blocks.push(
        <ul key={i} className="list-disc space-y-1 pl-5 text-sm text-ink-200">
          {items.map((item, n) => (
            <li key={n}>{inline(item.replace(/^\s*[-*]\s+/, ""))}</li>
          ))}
        </ul>,
      );
      continue;
    }
    if (line.startsWith("|")) {
      const rows = group((l) => l.startsWith("|"))
        .filter((l) => !/^\|[\s|:-]+\|$/.test(l))
        .map((l) => l.slice(1, -1).split("|").map((cell) => cell.trim()));
      const [head, ...body] = rows;
      blocks.push(
        <div key={i} className="overflow-x-auto">
          <table className="w-full text-left text-sm">
            <thead>
              <tr className="text-ink-400">
                {head.map((cell, n) => (
                  <th key={n} className="py-1.5 pr-4 font-medium">
                    {inline(cell)}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody className="divide-y divide-ink-800">
              {body.map((row, r) => (
                <tr key={r}>
                  {row.map((cell, n) => (
                    <td key={n} className={`py-1.5 pr-4 ${n ? "num text-ink-100" : "text-ink-300"}`}>
                      {inline(cell)}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>,
      );
      continue;
    }
    const paragraph = group(
      (l) => !!l.trim() && !/^(#{1,4}\s|>|\||\s*[-*]\s)/.test(l) && !/^-{3,}$/.test(l.trim()),
    );
    blocks.push(
      <p key={i} className="text-[15px] leading-relaxed text-ink-200">
        {inline(paragraph.join(" "))}
      </p>,
    );
  }
  return <div className="space-y-3">{blocks}</div>;
}

const TOKEN = /(\*\*[^*]+\*\*|\*[^*\s][^*]*\*|\[[^\]]+\]\(https?:\/\/[^)\s]+\))/g;

function inline(text: string): ReactNode[] {
  return text.split(TOKEN).map((part, n) => {
    if (part.startsWith("**") && part.endsWith("**")) {
      return (
        <strong key={n} className="font-semibold text-white">
          {part.slice(2, -2)}
        </strong>
      );
    }
    const link = /^\[([^\]]+)\]\((https?:\/\/[^)\s]+)\)$/.exec(part);
    if (link) {
      return (
        <a key={n} href={link[2]} target="_blank" rel="noreferrer noopener" className="text-accent hover:underline">
          {link[1]}
        </a>
      );
    }
    if (part.length > 2 && part.startsWith("*") && part.endsWith("*")) {
      return <em key={n}>{part.slice(1, -1)}</em>;
    }
    return part;
  });
}
