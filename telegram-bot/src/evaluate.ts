import type { Matrix, Node, Condition } from "./types";

function fieldValue(node: Node, field: Condition["field"]): unknown {
  switch (field) {
    case "country": return node.country;
    case "isp": return node.isp;
    case "protocol": return node.protocol;
    case "score": return node.score;
    case "ping": return node.tcp_ping_ms;
    case "speed": return node.speed_mbps;
    case "gemini": return node.gemini_reachable;
  }
}

function matchCondition(node: Node, c: Condition): boolean {
  const v = fieldValue(node, c.field);
  switch (c.operator) {
    case "eq": return v === c.value;
    case "neq": return v !== c.value;
    case "lt": return typeof v === "number" && v < (c.value as number);
    case "lte": return typeof v === "number" && v <= (c.value as number);
    case "gt": return typeof v === "number" && v > (c.value as number);
    case "gte": return typeof v === "number" && v >= (c.value as number);
    case "in":
      return Array.isArray(c.value) && c.value.includes(String(v));
    default: return false;
  }
}

export function evaluate(matrix: Matrix, nodes: Node[]): Node[] {
  return nodes.filter((node) => {
    if (matrix.conditions.length === 0) return true;
    return matrix.combinator === "AND"
      ? matrix.conditions.every((c) => matchCondition(node, c))
      : matrix.conditions.some((c) => matchCondition(node, c));
  });
}
