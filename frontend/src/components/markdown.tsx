import ReactMarkdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";

const components: Components = {
  p: ({ children }) => (
    <p className="mb-3 text-chat text-body last:mb-0">{children}</p>
  ),
  strong: ({ children }) => (
    <strong className="font-semibold text-ink">{children}</strong>
  ),
  em: ({ children }) => <em className="text-body">{children}</em>,
  ul: ({ children }) => (
    <ul className="mb-3 ml-1 space-y-1.5 text-chat text-body last:mb-0">
      {children}
    </ul>
  ),
  ol: ({ children }) => (
    <ol className="mb-3 ml-4 list-decimal space-y-1.5 text-chat text-body marker:text-muted-soft last:mb-0">
      {children}
    </ol>
  ),
  li: ({ children }) => (
    <li className="pl-1 marker:text-lemon-deep/70">{children}</li>
  ),
  h1: ({ children }) => (
    <h1 className="mb-2 mt-5 font-text text-title-md font-semibold text-ink first:mt-0">
      {children}
    </h1>
  ),
  h2: ({ children }) => (
    <h2 className="mb-2 mt-5 font-text text-title-sm font-semibold text-ink first:mt-0">
      {children}
    </h2>
  ),
  h3: ({ children }) => (
    <h3 className="mb-1.5 mt-4 text-[17px] font-semibold leading-[1.3] tracking-[-0.374px] text-ink first:mt-0">
      {children}
    </h3>
  ),
  a: ({ href, children }) => (
    <a
      href={href}
      target="_blank"
      rel="noreferrer"
      className="text-lemon-deep underline decoration-lemon/30 underline-offset-2 transition hover:decoration-lemon"
    >
      {children}
    </a>
  ),
  code: ({ children }) => (
    <code className="rounded border border-beige bg-surface-cream px-1.5 py-0.5 font-mono text-[0.85em] text-lemon-deep">
      {children}
    </code>
  ),
  pre: ({ children }) => (
    <pre className="mb-3 overflow-x-auto rounded-xl border border-surface-code bg-surface-code p-4 font-mono text-[13.5px] leading-6 text-on-dark [&_code]:border-0 [&_code]:bg-transparent [&_code]:p-0 [&_code]:text-on-dark">
      {children}
    </pre>
  ),
  blockquote: ({ children }) => (
    <blockquote className="mb-3 border-l-2 border-lemon/30 pl-4 italic text-muted [&_p]:mb-0">
      {children}
    </blockquote>
  ),
  hr: () => <hr className="my-5 border-hairline" />,
  table: ({ children }) => (
    <div className="mb-3 overflow-x-auto rounded-xl border border-hairline">
      <table className="w-full border-collapse text-left text-[14px]">
        {children}
      </table>
    </div>
  ),
  thead: ({ children }) => (
    <thead className="bg-surface-cream text-ink">{children}</thead>
  ),
  th: ({ children }) => (
    <th className="border-b border-hairline px-3 py-2 font-semibold">
      {children}
    </th>
  ),
  td: ({ children }) => (
    <td className="border-b border-hairline-soft px-3 py-2 text-body">
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
