import { LastTimeSchema } from "../validation_schemas/last-time";
import type { LastTimeResponse } from "../validation_schemas/last-time";
import { db } from "../database/db";
import { computers, desktopClients, clientVersions } from "../drizzle/schema";
import { eq } from "drizzle-orm";
import { logger } from "../utils/logger";
import { getCurrentTimestamp } from "../utils/timestamp";

export const lastTime = async (req: Request) => {
  const bodyRaw = await req.json();
  const body = LastTimeSchema.parse(bodyRaw);

  logger.info({ computer: body.computer_name }, "Last time update request");

  // Find computer by identifier_key if provided
  const computer = body.identifier_key
    ? await db.query.computers.findFirst({
        where: eq(computers.identifierKey, body.identifier_key),
        columns: {
          id: true,
          computerName: true,
          sftpHost: true,
          sftpUsername: true,
          sftpFolderPath: true,
          managerHost: true,
          msiVersion: true,
          logsEnabled: true,
        },
      })
    : null;

  if (!computer) {
    // Check if computer exists by name only (for better error message)
    const computerByName = await db.query.computers.findFirst({
      where: eq(computers.computerName, body.computer_name),
      columns: { id: true },
    });

    let message: string;
    let rmcreds: string | undefined;

    if (computerByName) {
      message = "Wrong id.";
      logger.info(
        {
          computer: body.computer_name,
          reason: message,
        },
        "Last download/online time update failed"
      );
    } else {
      message = "Wrong request data. Computer not found.";
      rmcreds = "rmcreds";
      logger.info(
        {
          computer: body.computer_name,
          reason: message,
        },
        "Last download/online time update failed. Removing local credentials"
      );
    }

    return new Response(
      JSON.stringify({ status: "fail", message, rmcreds } as LastTimeResponse),
      {
        status: 400,
        headers: { "Content-Type": "application/json" },
      }
    );
  }

  // Get client IP
  const clientIp =
    req.headers.get("X-Forwarded-For") ||
    req.headers.get("X-Real-IP") ||
    "unknown";

  // Format timestamp to match Flask format (YYYY-MM-DD HH:MM:SS without milliseconds)
  const now = getCurrentTimestamp();

  // Update computer timestamps (fire-and-forget for speed)
  const updateData: {
    computerIp: string;
    lastTimeOnline: string;
    lastDownloadTime?: string;
  } = {
    computerIp: clientIp,
    lastTimeOnline: now,
  };

  if (body.last_download_time) {
    updateData.lastDownloadTime = now;
  }

  try {
    await db
      .update(computers)
      .set(updateData)
      .where(eq(computers.id, computer.id));
  } catch (err) {
    logger.error(
      { err, computer: computer.id },
      "Failed to update computer timestamps"
    );
    return new Response(
      JSON.stringify({
        status: "fail",
        message: "Failed to save timestamps",
      } as LastTimeResponse),
      {
        status: 500,
        headers: { "Content-Type": "application/json" },
      }
    );
  }

  // Flask updates backup period logs (chart/history); file_api only updates timestamps
  if (body.last_download_time && body.identifier_key) {
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
          "Backup log sync failed"
        );
      }
    } catch (syncErr) {
      logger.warn(
        { err: syncErr, computer: computer.computerName },
        "Backup log sync request failed"
      );
    }
  }

  // Get MSI version - optimized to avoid loading blob
  const msi = await (async () => {
    if (computer.msiVersion === "stable" || computer.msiVersion === "latest") {
      // Find by flag name with JOIN, only select version
      const result = await db
        .select({
          version: desktopClients.version,
        })
        .from(clientVersions)
        .leftJoin(desktopClients, eq(desktopClients.flagId, clientVersions.id))
        .where(eq(clientVersions.name, computer.msiVersion))
        .limit(1);
      return result[0]?.version || null;
    } else if (computer.msiVersion) {
      // Find by version
      const result = await db.query.desktopClients.findFirst({
        where: eq(desktopClients.version, computer.msiVersion),
        columns: { version: true },
      });
      return result?.version || null;
    }
    return null;
  })();

  logger.info(
    { computer: computer.computerName },
    "Last time updated successfully"
  );

  const response: LastTimeResponse = {
    status: "success",
    message: "Writing time to db",
    sftp_host: computer.sftpHost || undefined,
    sftp_username: computer.sftpUsername || undefined,
    sftp_folder_path: computer.sftpFolderPath || "", // Always return, even if empty
    manager_host: computer.managerHost || undefined,
    msi_version: msi || "undefined",
  };

  return new Response(JSON.stringify(response), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });
};
