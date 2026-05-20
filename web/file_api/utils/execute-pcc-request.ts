import type { HeadersInit } from "bun";
import { checkDailyRequestsCount } from "./check-daily-requests-count";
import { logger } from "./logger";

const ERROR_BODY_LOG_MAX = 500;

async function logPccErrorResponse(
  response: Response,
  url: string,
): Promise<void> {
  const snippet = await response
    .clone()
    .text()
    .then((t) => t.slice(0, ERROR_BODY_LOG_MAX))
    .catch(() => "");

  logger.warn(
    {
      status: response.status,
      statusText: response.statusText,
      url,
      body: snippet || undefined,
    },
    "PCC request returned error status",
  );
}

/**
 * Call PCC with mTLS. Returns the real Response (including 401/4xx/5xx) so callers
 * can retry on 401. Network/TLS failures return 502.
 */
export const executePccRequest = async (
  url: string,
  headers: HeadersInit,
  method: string = "GET",
  body?: unknown,
): Promise<Response> => {
  const checkResult = await checkDailyRequestsCount();
  if (checkResult instanceof Response) {
    return checkResult;
  }

  try {
    const response = await fetch(url, {
      method,
      headers,
      body: body !== undefined ? JSON.stringify(body) : undefined,
      tls: {
        cert: await Bun.file(process.env.CERTIFICATE_PATH!).text(),
        key: await Bun.file(process.env.PRIVATEKEY_PATH!).text(),
      },
    });

    if (!response.ok) {
      await logPccErrorResponse(response, url);
    }

    return response;
  } catch (error) {
    logger.error({ err: error, url }, "PCC request failed (network or TLS)");
    return new Response(
      JSON.stringify({ error: "Error executing PCC request" }),
      {
        status: 502,
        headers: { "Content-Type": "application/json" },
      },
    );
  }
};
