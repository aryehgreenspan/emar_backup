import {
  describe,
  it,
  expect,
  beforeEach,
  afterEach,
  mock,
} from "bun:test";

const originalFetch = globalThis.fetch;
const certPath = process.env.CERTIFICATE_PATH || "/tmp/test-cert.pem";
const keyPath = process.env.PRIVATEKEY_PATH || "/tmp/test-key.pem";

mock.module("../utils/check-daily-requests-count", () => ({
  checkDailyRequestsCount: async () => true,
}));

describe("executePccRequest", () => {
  beforeEach(async () => {
    process.env.CERTIFICATE_PATH = certPath;
    process.env.PRIVATEKEY_PATH = keyPath;
    await Bun.write(certPath, "cert");
    await Bun.write(keyPath, "key");
  });

  afterEach(() => {
    globalThis.fetch = originalFetch;
  });

  it("returns the real HTTP status on PCC error (does not throw)", async () => {
    globalThis.fetch = mock(() =>
      Promise.resolve(
        new Response("unauthorized", {
          status: 401,
          statusText: "Unauthorized",
        }),
      ),
    ) as typeof fetch;

    const { executePccRequest } = await import("../utils/execute-pcc-request");
    const res = await executePccRequest(
      "https://example.test/backup-files",
      { Authorization: "Bearer old" },
      "GET",
    );

    expect(res.status).toBe(401);
    expect(res.ok).toBe(false);
  });

  it("returns 502 on network failure", async () => {
    globalThis.fetch = mock(() =>
      Promise.reject(new Error("ECONNREFUSED")),
    ) as typeof fetch;

    const { executePccRequest } = await import("../utils/execute-pcc-request");
    const res = await executePccRequest(
      "https://example.test/backup-files",
      {},
      "GET",
    );

    expect(res.status).toBe(502);
  });
});
