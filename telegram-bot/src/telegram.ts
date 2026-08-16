import { escapeHtml } from "./format";

const BASE = "https://api.telegram.org";

export async function sendMessage(
  token: string,
  chatId: number,
  text: string,
): Promise<void> {
  await fetch(`${BASE}/bot${token}/sendMessage`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ chat_id: chatId, text: escapeHtml(text), parse_mode: "HTML" }),
  });
}

export async function sendDocument(
  token: string,
  chatId: number,
  caption: string,
  filename: string,
  content: string,
): Promise<void> {
  const form = new FormData();
  form.set("chat_id", String(chatId));
  form.set("caption", caption);
  form.set("parse_mode", "HTML");
  form.set("document", new Blob([content], { type: "text/plain" }), filename);
  await fetch(`${BASE}/bot${token}/sendDocument`, {
    method: "POST",
    body: form,
  });
}
