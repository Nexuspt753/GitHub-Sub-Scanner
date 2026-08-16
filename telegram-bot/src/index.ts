export default {
  fetch(_request: Request, _env: unknown, _ctx: unknown): Response {
    return new Response("ok");
  },
};
