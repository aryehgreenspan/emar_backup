import { db } from "../database/db";
import { computers, downloadBackupCalls } from "../drizzle/schema";
import { eq, and } from "drizzle-orm";
import { PCCDownloadSchema } from "../validation_schemas/pcc";
import {
  getPcc2LeggedToken,
  invalidatePccAccessToken,
} from "../utils/get-pcc-2-legged-token";
import { executePccRequest } from "../utils/execute-pcc-request";
import { logger } from "../utils/logger";

const ERROR_BODY_LOG_MAX = 500;

async function pccErrorBodySnippet(response: Response): Promise<string | undefined> {
  const snippet = await response
    .clone()
    .text()
    .then((t) => t.slice(0, ERROR_BODY_LOG_MAX))
    .catch(() => "");
  return snippet || undefined;
}

export const downloadFromPCC = async (req: Bun.BunRequest) => {
  const bodyRaw = await req.json();
  const body = PCCDownloadSchema.parse(bodyRaw);

  const computer = await db.query.computers.findFirst({
    with: { company: true, location: true },
    where: and(
      eq(computers.identifierKey, body.identifier_key),
      eq(computers.computerName, body.computer_name)
    ),
  });
  logger.info(
    { computer: computer?.computerName },
    "downloadFromPCC - computer found"
  );

  if (!computer) {
    return new Response(
      `Computer with such credentials not found. Computer_name: ${body.computer_name}, identifier_key: ${body.identifier_key}`,
      { status: 404 }
    );
  }

  if (!computer.company || !computer.location) {
    logger.error(
      { computerId: computer.id },
      "Can't download backup for computer. Company or location is not set"
    );
    return new Response("Company or location is not set", { status: 409 });
  }

  if (computer.company.isTrial || !computer.activated) {
    logger.error(
      {
        computerId: computer.id,
        isTrial: computer.company.isTrial,
        activated: computer.activated,
      },
      "Can't download backup for computer. Company is trial or computer is not activated"
    );
    return new Response("Company is trial or computer is not activated", {
      status: 403,
    });
  }

  if (!computer.company.pccOrgId || !computer.location.pccFacId) {
    logger.error(
      {
        computerId: computer.id,
        pccOrgId: computer.company.pccOrgId,
        pccFacId: computer.location.pccFacId,
      },
      "Can't download backup for computer. PCC_ORG_ID or PCC_FAC_ID is not set"
    );
    return new Response("PCC_ORG_ID or PCC_FAC_ID is not set", { status: 409 });
  }

  const token = await getPcc2LeggedToken();

  // Download backup file
  const backupRoute = `api/public/preview1/orgs/${computer.company.pccOrgId}/facs/${body.pcc_fac_id}/backup-files`;
  const baseUrl = process.env.PCC_BASE_URL!;
  const url = `${
    baseUrl.endsWith("/") ? baseUrl.slice(0, -1) : baseUrl
  }/${backupRoute}`;
  const headers = {
    Authorization: `Bearer ${token}`,
    "Content-Type": "application/json",
  };

  let res = await executePccRequest(url, headers, "GET");
  logger.info(
    { status: res.status, ok: res.ok, computer: computer.computerName },
    "downloadFromPCC - PCC response received"
  );

  // Retry logic for 401 Unauthorized - token might be expired (max 1 retry)
  if (res.status === 401) {
    logger.warn(
      { computerId: computer.id },
      "Got 401 from PCC, deleting old token and retrying with new one (attempt 1/1)"
    );

    await invalidatePccAccessToken();
    const newToken = await getPcc2LeggedToken();

    // Retry request with new token (only once)
    headers.Authorization = `Bearer ${newToken}`;
    res = await executePccRequest(url, headers, "GET");

    logger.info(
      {
        status: res.status,
        ok: res.ok,
        computer: computer.computerName,
        retryAttempt: 1,
      },
      "downloadFromPCC - PCC response after retry"
    );
  }
  if (!res.ok) {
    logger.error(
      {
        computerId: computer.id,
        status: res.status,
        body: await pccErrorBodySnippet(res),
      },
      "Error downloading backup from PCC for computer"
    );
    return new Response(`Error downloading backup from PCC`, {
      status: res.status,
    });
  }

  //     # Create a record about new backup call
  await db.insert(downloadBackupCalls).values({
    computerId: computer.id,
    createdAt: new Date().toISOString(),
  });

  // Return the response as a stream with chunked transfer
  // This is equivalent to Python's res.iter_content(chunk_size=10 * 1024)
  // Bun automatically handles chunked streaming when you pass a ReadableStream
  return new Response(res.body, {
    status: res.status,
    headers: {
      "Content-Type":
        res.headers.get("Content-Type") || "application/octet-stream",
      "Content-Disposition": "attachment; filename=emar_backup.zip",
      // Copy other important headers from the original response
      "Content-Length": res.headers.get("Content-Length") || undefined,
      "Transfer-Encoding": "chunked", // Enable chunked transfer like Python's streaming
    },
  });
};
