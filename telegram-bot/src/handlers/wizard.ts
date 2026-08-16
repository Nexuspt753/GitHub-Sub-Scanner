import { getConv, putConv, deleteConv, putSubscriber } from "../kv";
import type { Condition, Matrix, SubscriberRecord } from "../types";

interface Button { text: string; callback_data: string }
type SendFn = (text: string, keyboard?: Button[][]) => Promise<void>;

interface WizardState {
  step: "field" | "operator" | "value" | "combinator" | "mode";
  conditions: Condition[];
  combinator: Matrix["combinator"];
}

const FIELDS: Condition["field"][] = [
  "country", "isp", "protocol", "score", "ping", "speed", "gemini",
];
const OPERATORS: Condition["operator"][] = [
  "eq", "neq", "lt", "lte", "gt", "gte", "in",
];

export class Wizard {
  constructor(
    private kv: any,
    private chatId: number,
  ) {}

  async start(send: SendFn): Promise<void> {
    const state: WizardState = { step: "field", conditions: [], combinator: "AND" };
    await this.save(state);
    await send("Step 1/5 — pick a field:", this.kb(FIELDS, "field"));
  }

  async handleCallback(data: string, send: SendFn): Promise<void> {
    const state = await getConv(this.kv, this.chatId) as WizardState | null;
    if (!state) return this.start(send);

    if (state.step === "field") {
      const field = data.split(":")[1] as Condition["field"];
      state.conditions.push({ field, operator: "eq", value: "" });
      state.step = "operator";
      await this.save(state);
      await send("Step 2/5 — pick an operator:", this.kb(OPERATORS, "op"));
    } else if (state.step === "operator") {
      const operator = data.split(":")[1] as Condition["operator"];
      state.conditions[state.conditions.length - 1].operator = operator;
      state.step = "value";
      await this.save(state);
      await send("Step 3/5 — send the value (e.g. United Kingdom, or 50):");
    } else if (state.step === "combinator") {
      const [comb, action] = data.split(":").slice(1);
      state.combinator = comb as Matrix["combinator"];
      if (action === "done") {
        state.step = "mode";
        await this.save(state);
        await send(
          "Step 4/5 — pick push mode: diff (only new matches) or digest (regular top-5):",
          this.kb(["diff", "digest"], "mode"),
        );
      } else {
        state.step = "field";
        await this.save(state);
        await send("Next condition — pick a field:", this.kb(FIELDS, "field"));
      }
    } else if (state.step === "mode") {
      const mode = data.split(":")[1] as Matrix["mode"];
      await this.confirm(state, mode, send);
    }
  }

  async handleValue(text: string, send: SendFn): Promise<void> {
    const state = await getConv(this.kv, this.chatId) as WizardState | null;
    if (!state) return this.start(send);
    const cond = state.conditions[state.conditions.length - 1];
    const raw = text.trim();
    cond.value = (cond.operator === "in")
      ? raw.split(",").map((s) => s.trim())
      : (["lt", "lte", "gt", "gte"].includes(cond.operator) ? Number(raw) : raw);
    state.step = "combinator";
    await this.save(state);
    await send(
      "Step 3/5 done — add another condition, or finish?",
      this.kb(["AND, finish", "OR, finish", "AND, add more", "OR, add more"], "comb"),
    );
  }

  private async confirm(state: WizardState, mode: Matrix["mode"], send: SendFn): Promise<void> {
    const matrix: Matrix = {
      conditions: state.conditions,
      combinator: state.combinator,
      mode,
    };
    const rec: SubscriberRecord = {
      chatId: this.chatId,
      matrix,
      createdAt: Date.now(),
      lastNotifiedAt: 0,
    };
    await putSubscriber(this.kv, rec);
    await deleteConv(this.kv, this.chatId);
    await send(
      `Subscribed! ${state.conditions.length} condition(s), ${mode} mode. I'll DM you matching configs.`,
    );
  }

  private async save(state: WizardState): Promise<void> {
    await putConv(this.kv, this.chatId, {
      step: state.step,
      conditions: state.conditions,
    });
  }

  private kb(options: string[], prefix: string): Button[][] {
    return options.map((o) => [{ text: o, callback_data: `${prefix}:${o}` }]);
  }
}
