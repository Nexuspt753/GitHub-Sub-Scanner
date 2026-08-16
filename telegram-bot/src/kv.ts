import type {
  SubscriberRecord, Node, Matrix,
} from "./types";

export interface ConvState {
  step: string;
  conditions: Matrix["conditions"];
}

export interface ResultsCache {
  fetchedAt: number;
  nodes: Node[];
}

type KV = {
  get(k: string, t?: string): Promise<unknown>;
  put(k: string, v: string): Promise<void>;
  delete(k: string): Promise<void>;
  list(o: { prefix: string }): Promise<{ keys: { name: string }[] }>;
};

export async function getSubscriber(kv: KV, chatId: number): Promise<SubscriberRecord | null> {
  const v = await kv.get(`sub:${chatId}`, "json");
  return v as SubscriberRecord | null;
}

export async function putSubscriber(kv: KV, rec: SubscriberRecord): Promise<void> {
  await kv.put(`sub:${rec.chatId}`, JSON.stringify(rec));
}

export async function deleteSubscriber(kv: KV, chatId: number): Promise<void> {
  await kv.delete(`sub:${chatId}`);
}

export async function listSubscribers(kv: KV): Promise<SubscriberRecord[]> {
  const { keys } = await kv.list({ prefix: "sub:" });
  const recs = await Promise.all(keys.map((k) => kv.get(k.name, "json")));
  return recs.filter(Boolean) as SubscriberRecord[];
}

export async function getResults(kv: KV): Promise<ResultsCache | null> {
  const v = await kv.get("results:cache", "json");
  return v as ResultsCache | null;
}

export async function putResults(kv: KV, nodes: Node[]): Promise<void> {
  const cache: ResultsCache = { fetchedAt: Date.now(), nodes };
  await kv.put("results:cache", JSON.stringify(cache));
}

export async function getConv(kv: KV, chatId: number): Promise<ConvState | null> {
  const v = await kv.get(`conv:${chatId}`, "json");
  return v as ConvState | null;
}

export async function putConv(kv: KV, chatId: number, state: ConvState): Promise<void> {
  await kv.put(`conv:${chatId}`, JSON.stringify(state));
}

export async function deleteConv(kv: KV, chatId: number): Promise<void> {
  await kv.delete(`conv:${chatId}`);
}

export async function getShare(kv: KV, token: string): Promise<Matrix | null> {
  const v = await kv.get(`share:${token}`, "json");
  return v as Matrix | null;
}

export async function putShare(kv: KV, token: string, matrix: Matrix): Promise<void> {
  await kv.put(`share:${token}`, JSON.stringify(matrix));
}
