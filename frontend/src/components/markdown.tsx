import ReactMarkdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";

const components: Components = {
  p: ({ children }) => (
    <p className="mb-3 text-[15px] leading-7 text-stone-300 last:mb-0">
      {children}
    </p>
  ),
  strong: ({ children }) => (
    <strong className="font-semibold text-stone-100">{children}</strong>
  ),
  em: ({ children }) => <em className="text-stone-300">{children}</em>,
  ul: ({ children }) => (
    <ul className="mb-3 ml-1 space-y-1.5 text-[15px] leading-7 text-stone-300 last:mb-0">
      {children}
    </ul>
  ),
  ol: ({ children }) => (
    <ol className="mb-3 ml-4 list-decimal space-y-1.5 text-[15px] leading-7 text-stone-300 marker:text-stone-500 last:mb-0">
      {children}
    </ol>
  ),
  li: ({ children }) => (
    <li className="pl-1 marker:text-emerald-300/80">{children}</li>
  ),
  h1: ({ children }) => (
    <h1 className="mb-2 mt-5 font-serif text-2xl text-stone-100 first:mt-0">
      {children}
    </h1>
  ),
  h2: ({ children }) => (
    <h2 className="mb-2 mt-5 font-serif text-xl text-stone-100 first:mt-0">
      {children}
    </h2>
  ),
  h3: ({ children }) => (
    <h3 className="mb-1.5 mt-4 text-[15px] font-semibold text-stone-100 first:mt-0">
      {children}
    </h3>
  ),
  a: ({ href, children }) => (
    <a
      href={href}
      target="_blank"
      rel="noreferrer"
      className="font-medium text-emerald-200 underline decoration-emerald-300/30 underline-offset-2 transition hover:decoration-emerald-300/80"
    >
      {children}
    </a>
  ),
  code: ({ children }) => (
    <code className="rounded border border-white/10 bg-white/[0.06] px-1.5 py-0.5 font-mono text-[0.85em] text-amber-100">
      {children}
    </code>
  ),
  pre: ({ children }) => (
    <pre className="mb-3 overflow-x-auto rounded-xl border border-white/10 bg-black/40 p-4 text-[13px] leading-6 text-stone-300 [&_code]:border-0 [&_code]:bg-transparent [&_code]:p-0 [&_code]:text-stone-300">
      {children}
    </pre>
  ),
  blockquote: ({ children }) => (
    <blockquote className="mb-3 border-l-2 border-emerald-300/40 pl-4 text-stone-400 italic">
      {children}
    </blockquote>
  ),
  hr: () => <hr className="my-5 border-white/10" />,
  table: ({ children }) => (
    <div className="mb-3 overflow-x-auto rounded-xl border border-white/10">
      <table className="w-full border-collapse text-left text-[13px]">
        {children}
      </table>
    </div>
  ),
  thead: ({ children }) => (
    <thead className="bg-white/[0.04] text-stone-200">{children}</thead>
  ),
  th: ({ children }) => (
    <th className="border-b border-white/10 px-3 py-2 font-medium">
      {children}
    </th>
  ),
  td: ({ children }) => (
    <td className="border-b border-white/5 px-3 py-2 text-stone-300">
      {children}
    </td>
  ),
};

export function Markdown({ children }: { children: string }) {
  return (
    <ReactMarkdown remarkPlugins={[remarkGfm]} components={components}>
      {children}
    </ReactMarkdown>
  );
}
