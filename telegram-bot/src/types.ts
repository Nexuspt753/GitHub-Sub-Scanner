export type Field =
  | "country" | "isp" | "protocol" | "score"
  | "ping" | "speed" | "gemini";

export type Operator = "eq" | "neq" | "lt" | "lte" | "gt" | "gte" | "in";

export interface Condition {
  field: Field;
  operator: Operator;
  value: string | number | boolean | string[];
}

export interface Matrix {
  conditions: Condition[];
  combinator: "AND" | "OR";
  mode: "diff" | "digest";
}

export interface Node {
  name: string | null;
  protocol: string;
  address: string;
  port: number;
  alive: boolean;
  tcp_ping_ms: number | null;
  speed_mbps: number | null;
  gemini_reachable: boolean | null;
  score: number;
  country: string | null;
  region: string | null;
  city: string | null;
  isp: string | null;
  uri: string;
}

export interface SubscriberRecord {
  chatId: number;
  matrix: Matrix;
  createdAt: number;
  lastNotifiedAt: number;
  lastNotifiedIds?: string[];
}

export interface ResultsPayload {
  nodes: Node[];
}
