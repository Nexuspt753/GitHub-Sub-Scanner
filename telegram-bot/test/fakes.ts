export class FakeKV {
  store = new Map<string, string>();

  async get(key: string, type?: string): Promise<unknown> {
    const v = this.store.get(key);
    if (v === undefined) return null;
    return type === "json" ? JSON.parse(v) : v;
  }

  async put(key: string, value: string): Promise<void> {
    this.store.set(key, value);
  }

  async delete(key: string): Promise<void> {
    this.store.delete(key);
  }

  async list(opts: { prefix: string }): Promise<{ keys: { name: string }[] }> {
    const keys = [...this.store.keys()]
      .filter((k) => k.startsWith(opts.prefix))
      .map((name) => ({ name }));
    return { keys };
  }
}

export class FakeTelegram {
  calls: { method: string; body: unknown }[] = [];

  async call(method: string, body: unknown): Promise<{ ok: boolean }> {
    this.calls.push({ method, body });
    return { ok: true };
  }
}
