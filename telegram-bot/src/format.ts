import type { Node } from "./types";

export function escapeHtml(str: string | null | undefined): string {
  return String(str ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
}

export function buildTxt(nodes: Node[]): string {
  return nodes.map((n) => n.uri).join("\n");
}

export function makeCaption(nodes: Node[], label: string): string {
  if (nodes.length === 0) return "0 configs";
  const top = nodes.slice(0, 3).map((n) => n.country ?? "?").join(", ");
  return `${escapeHtml(String(nodes.length))} configs · ${escapeHtml(label)} · e.g. ${escapeHtml(top)}`;
}
