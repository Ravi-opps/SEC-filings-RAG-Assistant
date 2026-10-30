import type { LinkType, RouteType } from "./types";

export const ROUTE_META: Record<
  RouteType,
  { label: string; description: string; tone: string }
> = {
  retrieval: {
    label: "Grounded",
    description: "Answered from indexed filing passages",
    tone: "border-lemon/25 bg-lemon/10 text-lemon",
  },
  conversational: {
    label: "Direct",
    description: "Answered without filing retrieval",
    tone: "border-hairline bg-surface-soft text-muted",
  },
  blocked: {
    label: "Guardrail",
    description: "Stopped by the input guardrail",
    tone: "border-warn/25 bg-warn/10 text-warn",
  },
};

export const LINK_TYPE_LABEL: Record<LinkType, string> = {
  anchor: "Anchor",
  text_fragment: "Passage",
  document: "Document",
};

export function relativeTime(timestamp: number): string {
  const seconds = Math.round((Date.now() - timestamp) / 1000);
  if (seconds < 45) return "just now";
  if (seconds < 3_600) return `${Math.round(seconds / 60)}m ago`;
  if (seconds < 86_400) return `${Math.round(seconds / 3_600)}h ago`;
  if (seconds < 604_800) return `${Math.round(seconds / 86_400)}d ago`;
  return new Date(timestamp).toLocaleDateString(undefined, {
    month: "short",
    day: "numeric",
  });
}

export function hostOf(url: string): string {
  try {
    return new URL(url).hostname.replace(/^www\./, "");
  } catch {
    return "sec.gov";
  }
}

export function relevancePercent(score?: number | null): number | null {
  if (score === null || score === undefined || Number.isNaN(score)) {
    return null;
  }
  const percent = score <= 1 ? score * 100 : score;
  return Math.max(0, Math.min(100, Math.round(percent)));
}

export function sourceLocation(
  parts: Array<string | number | null | undefined>,
): string {
  return parts.filter(Boolean).join(" / ");
}
