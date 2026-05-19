import datetime
import json

import zoneinfo
from flask import jsonify, request

from app.controllers import (
    backup_log_on_download_error,
    backup_log_on_download_success,
    create_log_event,
)
from app.controllers.backup_log import (
    backup_log_on_download_error_with_message,
    sync_backup_log_for_computer,
)
from app.logger import logger
from app.models import Computer, DesktopClient, LogType
from app.models.computer import PrinterStatus
from app.schema import (
    ComputerCredentialsInfo,
    DownloadStatus,
    FilesChecksum,
    GetCredentials,
    LastTime,
)
from app.schema.backup_log_sync import BackupLogSyncRequest
from app.schema.printer_info import PrinterInfo
from app.views.blueprint import BlueprintApi
from config import BaseConfig as CFG

downloads_info_blueprint = BlueprintApi("/downloads_info", __name__)


def check_msi_version(computer: Computer, body, time_type: str):
    """Check for old or new msi version. If old - return datetime.now(timezone.utc) + 5
    Else - return datetime.now(timezone.utc) + 4

    Args:
        computer (Computer): sqla Computer obj
        time_type (str): "online" or "download"

    Raises:
        ValueError: if time_type != "online" or "download"

    Returns:
        (datetime or str): computer.last_time_online or computer.last_download_time
    """
    lst_times = {
        "online": body.last_time_online,
        "download": body.last_download_time,
    }

    if time_type not in lst_times:
        raise ValueError("Wrong time_type in check_msi_version: {}", time_type)

    current_comp_msi_version = (
        computer.msi_version if computer.msi_version else "stable"
    )

    msi_version: DesktopClient = (
        DesktopClient.query.filter(DesktopClient.flag_name == current_comp_msi_version)
        .with_entities(DesktopClient.version)
        .first()
        if current_comp_msi_version == "stable" or current_comp_msi_version == "latest"
        else DesktopClient.query.filter(
            DesktopClient.version == current_comp_msi_version
        )
        .with_entities(DesktopClient.version)
        .first()
    )

    msi_version: DesktopClient = (
        msi_version
        if msi_version
        else DesktopClient.query.filter(DesktopClient.flag_name == "stable")
        .with_entities(DesktopClient.version)
        .first()
    )

    if int(msi_version.version.replace(".", "")) < int("1.0.9.100769".replace(".", "")):
        return lst_times[time_type] + datetime.timedelta(hours=1)
    return lst_times[time_type]


@downloads_info_blueprint.post("/last_time")
@logger.catch
def last_time(body: LastTime):
    # TODO use some token to secure api routes

    computer: Computer = (
        Computer.query.filter_by(identifier_key=body.identifier_key).first()
        if body.identifier_key
        else None
    )

    computer_name: Computer = Computer.query.filter_by(
        computer_name=body.computer_name
    ).first()

    if computer:
        computer.computer_ip = request.headers.get(
            "X-Forwarded-For", request.remote_addr
        )

        computer.last_time_online = CFG.offset_to_est(datetime.datetime.utcnow(), True)
        # field = "online"
        if body.last_download_time:
            computer.last_download_time = CFG.offset_to_est(
                datetime.datetime.utcnow(), True
            )
            # field = "download/online"
        computer.update()

        # TODO enable if required
        # logger.info(
        #     "Last {} time for computer {} is updated. New time download: {}. New time online: {}.",
        #     field,
        #     computer.computer_name,
        #     computer.last_download_time,
        #     computer.last_time_online,
        # )

        # Add uptime/downtime log event
        if computer.logs_enabled:
            create_log_event(
                computer, LogType.HEARTBEAT, created_at=computer.last_time_online
            )

        # Add or update backup period log and create BACKUP_DOWNLOAD log event
        if computer.logs_enabled and body.last_download_time:
            create_log_event(
                computer,
                LogType.BACKUP_DOWNLOAD,
                created_at=computer.last_download_time,
            )

            utc_download_time = computer.last_download_time.replace(
                tzinfo=zoneinfo.ZoneInfo("America/New_York")
            ).astimezone(zoneinfo.ZoneInfo("UTC"))
            backup_log_on_download_success(
                computer, utc_download_time.replace(tzinfo=None)
            )

        msi: DesktopClient = (
            DesktopClient.query.filter_by(flag_name=computer.msi_version).first()
            if computer.msi_version == "stable" or computer.msi_version == "latest"
            else DesktopClient.query.filter_by(version=computer.msi_version).first()
        )

        return (
            jsonify(
                status="success",
                message="Writing time to db",
                sftp_host=computer.sftp_host,
                sftp_username=computer.sftp_username,
                sftp_folder_path=computer.sftp_folder_path,
                manager_host=computer.manager_host,
                msi_version=msi.version if msi else "undefined",
            ),
            200,
        )

    elif computer_name:
        message = "Wrong id."
        logger.info(
            "Last download/online time update failed. computer: {}, \
            id {}. Reason: {}",
            body.computer_name,
            body.identifier_key,
            message,
        )
        return jsonify(status="fail", message=message), 400

    message = "Wrong request data. Computer not found."
    logger.info(
        "Last download/online time update failed. computer: {}, id {}. \
        Reason: {}. Removing local credentials.",
        body.computer_name,
        body.identifier_key,
        message,
    )
    return jsonify(status="fail", message=message, rmcreds="rmcreds"), 400


@downloads_info_blueprint.post("/sync_backup_log")
@logger.catch
def sync_backup_log(body: BackupLogSyncRequest):
    """Called by file_api after a successful backup timestamp update."""
    computer: Computer | None = Computer.query.filter_by(
        identifier_key=body.identifier_key
    ).first()

    if not computer:
        return jsonify(status="fail", message="Computer not found"), 404

    sync_backup_log_for_computer(computer)
    return jsonify(status="success"), 200


@downloads_info_blueprint.post("/get_credentials")
@logger.catch
def get_credentials(body: GetCredentials):
    logger.info(
        "Request for credentials. computer: {}, id: {}",
        body.computer_name,
        body.identifier_key,
    )
    # TODO add unique guid to headers in server_connect.py for api
    computer: Computer = (
        Computer.query.filter_by(
            identifier_key=body.identifier_key,
            computer_name=body.computer_name,
            is_deleted=False,
        ).first()
        if body.identifier_key
        else None
    )

    computer_name: Computer = Computer.query.filter_by(
        computer_name=body.computer_name,
    ).first()

    if computer:
        # If computer is not activated - prevent it from getting creds
        if not computer.activated:
            message = "Computer is not activated."
            logger.info(
                "Supplying credentials failed. computer: {}, \
                id {}. Reason: {}",
                body.computer_name,
                body.identifier_key,
                message,
            )
            return jsonify(status="fail", message=message), 400

        computer.computer_ip = request.headers.get(
            "X-Forwarded-For", request.remote_addr
        )
        computer.last_time_online = CFG.offset_to_est(
            datetime.datetime.now(datetime.UTC), True
        )
        # TODO find out why some computers can't write identifier_key to creds.json
        # TODO disable till then
        # computer.identifier_key = str(uuid.uuid4())
        computer.update()
        logger.info("Supplying credentials for computer {}.", computer.computer_name)

        remote_files_checksum = (
            computer.files_checksum if computer.files_checksum else {}
        )

        msi: DesktopClient = (
            DesktopClient.query.filter_by(flag_name=computer.msi_version).first()
            if computer.msi_version == "stable" or computer.msi_version == "latest"
            else DesktopClient.query.filter_by(version=computer.msi_version).first()
        )
        cred_info = ComputerCredentialsInfo(
            status="success",
            message="Supplying credentials",
            host=computer.sftp_host,
            company_name=computer.company_name,
            location_name=computer.location_name if computer.location_name else "",
            additional_locations=computer.get_additional_locations,
            sftp_username=computer.sftp_username,
            sftp_password=computer.sftp_password,
            sftp_folder_path=computer.sftp_folder_path,
            additional_folder_paths=[],
            identifier_key=computer.identifier_key,
            computer_name=computer.computer_name,
            folder_password=computer.folder_password,
            manager_host=computer.manager_host,
            device_location=computer.device_location,
            files_checksum=json.loads(str(remote_files_checksum)),
            msi_version=msi.version if msi else "undefined",
            version=msi.version if msi else "undefined",
            use_pcc_backup=computer.location.use_pcc_backup
            if computer.location
            else False,
            pcc_fac_id=computer.location.pcc_fac_id if computer.location else None,
        )

        return (
            jsonify(
                cred_info.dict(),
            ),
            200,
        )

    if computer_name:
        message = "Wrong id."
        logger.info(
            "Supplying credentials failed. computer: {}, \
            id {}. Reason: {}",
            body.computer_name,
            body.identifier_key,
            message,
        )
        return jsonify(status="fail", message=message), 400

    message = "Wrong request data. Computer not found."
    logger.info(
        "Supplying credentials failed. computer: {}, id {}. \
        Reason: {}. Removing local credentials.",
        body.computer_name,
        body.identifier_key,
        message,
    )
    return jsonify(status="fail", message=message, rmcreds="rmcreds"), 400


@downloads_info_blueprint.post("/download_status")
@logger.catch
def download_status(body: DownloadStatus):
    computer: Computer = (
        Computer.query.filter_by(identifier_key=body.identifier_key).first()
        if body.identifier_key
        else None
    )

    if computer:
        computer.last_time_online = CFG.offset_to_est(datetime.datetime.utcnow(), True)
        computer.download_status = body.download_status
        if body.last_downloaded:
            computer.last_downloaded = body.last_downloaded

        if body.last_saved_path:
            computer.last_saved_path = body.last_saved_path

        computer.update()

        logger.info(
            "Download status for computer {} is updated to {}.",
            computer.computer_name,
            computer.download_status,
        )

        if (
            computer.logs_enabled
            and body.download_status == "error"
            and body.error_message == ""
        ):
            backup_log_on_download_error(computer)
        elif (
            computer.logs_enabled
            and body.download_status == "error"
            and body.error_message != ""
        ):
            backup_log_on_download_error_with_message(computer, body.error_message)
        return jsonify(status="success", message="Writing download status to db"), 200

    message = "Wrong request data. Computer not found."
    logger.info(
        "Download status update failed. company_name: {}, \
        location {}. Reason: {}",
        body.company_name,
        body.location_name,
        message,
    )
    return jsonify(status="fail", message=message), 400


@downloads_info_blueprint.post("/files_checksum")
@logger.catch
def files_checksum(body: FilesChecksum):
    computer: Computer = (
        Computer.query.filter_by(identifier_key=body.identifier_key).first()
        if body.identifier_key
        else None
    )

    if computer:
        logger.info("Updating files checksum for computer: {}.", computer.computer_name)
        computer.last_time_online = CFG.offset_to_est(datetime.datetime.utcnow(), True)
        computer.files_checksum = json.dumps(body.files_checksum)
        computer.update()
        # TODO enable if required
        # logger.debug(
        #     "Files checksum for computer {} is updated to {}.",
        #     computer.computer_name,
        #     body.files_checksum,
        # )

        return jsonify(status="success", message="Writing files checksum to db"), 200

    message = "Wrong request data. Computer not found."
    logger.info(f"Files checksum update failed. Reason: {message}")
    return jsonify(status="fail", message=message), 400


@downloads_info_blueprint.post("/printer_info")
@logger.catch
def printer_info(body: PrinterInfo):
    computer: Computer = (
        Computer.query.filter_by(identifier_key=body.identifier_key).first()
        if body.identifier_key
        else None
    )

    if computer:
        print_status = PrinterStatus.UNKNOWN

        # TODO: fill this match with gathered data from customers pc
        match body.printer_info.PrinterStatus:
            case 0:
                print_status = PrinterStatus.NORMAL
            case 128:
                print_status = PrinterStatus.OFFLINE
            case _:
                print_status = PrinterStatus.UNKNOWN

        computer.printer_name = body.printer_info.Name
        computer.printer_status = print_status
        computer.printer_status_timestamp = CFG.offset_to_est(
            datetime.datetime.utcnow(), True
        )

        computer.update()

        return (
            jsonify(
                status="success",
                message="Writing printer info to db",
            ),
            200,
        )
    else:
        message = "Wrong request data. Computer not found."
        logger.info(f"Printer info update failed. Reason: {message}")
        return jsonify(status="fail", message=message), 400
