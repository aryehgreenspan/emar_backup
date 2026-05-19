import { DownloadStatusSchema } from "../validation_schemas/download-status";
import type { DownloadStatusResponse } from "../validation_schemas/download-status";
import { db } from "../database/db";
import { computers } from "../drizzle/schema";
import { eq } from "drizzle-orm";
import { logger } from "../utils/logger";
import { getCurrentTimestamp } from "../utils/timestamp";

export const downloadStatus = async (req: Request) => {
  let body;

  try {
    const bodyRaw = await req.json();
    body = DownloadStatusSchema.parse(bodyRaw);
  } catch (error) {
    logger.info({ error: String(error) }, "Download status validation failed");
    return new Response(
      JSON.stringify({
        status: "fail",
        message: "Invalid request data",
      } as DownloadStatusResponse),
      {
        status: 400,
        headers: { "Content-Type": "application/json" },
      }
    );
  }

  const computer = await db.query.computers.findFirst({
    where: eq(computers.identifierKey, body.identifier_key),
    columns: {
      id: true,
      computerName: true,
      logsEnabled: true,
    },
  });

  if (!computer) {
    const message = "Wrong request data. Computer not found.";
    logger.info(
      {
        company_name: body.company_name,
        location_name: body.location_name,
        reason: message,
      },
      "Download status update failed"
    );

    return new Response(
      JSON.stringify({ status: "fail", message } as DownloadStatusResponse),
      {
        status: 400,
        headers: { "Content-Type": "application/json" },
      }
    );
  }
  logger.info(
    {
      computer: computer.computerName,
    },
    "Download status update request"
  );

  // Prepare update data
  const now = getCurrentTimestamp();
  const updateData: {
    lastTimeOnline: string;
    downloadStatus: string;
    lastDownloadTime?: string;
    lastDownloaded?: string;
    lastSavedPath?: string;
  } = {
    lastTimeOnline: now,
    downloadStatus: body.download_status,
  };

  if (body.download_status === "downloaded") {
    updateData.lastDownloadTime = now;
  }

  if (body.last_downloaded) {
    updateData.lastDownloaded = body.last_downloaded;
  }

  if (body.last_saved_path) {
    updateData.lastSavedPath = body.last_saved_path;
  }

  // Update computer
  await db
    .update(computers)
    .set(updateData)
    .where(eq(computers.id, computer.id));

  if (body.download_status === "downloaded" && body.identifier_key) {
    const flaskBase =
      process.env.FLASK_INTERNAL_URL?.replace(/\/$/, "") || "http://app:5000";
    try {
      const syncRes = await fetch(`${flaskBase}/sync_backup_log`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ identifier_key: body.identifier_key }),
      });
      if (!syncRes.ok) {
        logger.warn(
          { computer: computer.computerName, status: syncRes.status },
          "Backup log sync failed after download_status"
        );
      }
    } catch (syncErr) {
      logger.warn(
        { err: syncErr, computer: computer.computerName },
        "Backup log sync request failed after download_status"
      );
    }
  }

  logger.info(
    {
      computer: computer.computerName,
      downloadStatus: body.download_status,
    },
    "Download status for computer is updated"
  );

  // TODO: Handle backup logs on error
  // if (computer.logsEnabled && body.download_status === "error") {
  //   if (!body.error_message) {
  //     await backupLogOnDownloadError(computer);
  //   } else {
  //     await backupLogOnDownloadErrorWithMessage(computer, body.error_message);
  //   }
  // }

  const response: DownloadStatusResponse = {
    status: "success",
    message: "Writing download status to db",
  };

  return new Response(JSON.stringify(response), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });
};
