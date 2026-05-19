from datetime import datetime, timedelta

from flask import Blueprint, abort, current_app, render_template, request
from flask_login import current_user, login_required

from app import db
from app import models as m
from app.controllers import create_pagination, sync_backup_log_for_computer
from config import BaseConfig as CFG

from .utils import has_access_to_company, has_access_to_computer, has_access_to_location

info_blueprint = Blueprint("info", __name__, url_prefix="/info")


def _format_duration(delta: timedelta) -> str:
    """Format timedelta like BackupLog.duration_as_str (e.g. '0 days 1 hours 25 minutes')."""
    if delta.total_seconds() < 0:
        delta = timedelta(0)
    total_seconds = int(delta.total_seconds())
    days = total_seconds // 86400
    remainder = total_seconds % 86400
    hours = remainder // 3600
    minutes = (remainder % 3600) // 60
    return f"{days} days {hours} hours {minutes} minutes"


@info_blueprint.route("/computer/<int:computer_id>", methods=["GET"])
@login_required
def computer_info(computer_id):
    # Query params from computers list page for "Go back" button
    LOCAL_PARAMS_NAMES = ["chart_days", "per_page", "q", "page"]
    computers_list_params = []
    for arg_name, arg_value in request.args.items():
        if arg_name not in LOCAL_PARAMS_NAMES:
            computers_list_params.append(f"{arg_name}={arg_value}")

    computers_search_params = "&".join(computers_list_params)

    computer = m.Computer.query.filter_by(id=computer_id).first_or_404()

    # Check if user has access to computer information
    if not has_access_to_computer(current_user, computer):
        abort(403, "You don't have access to this computer information.")

    # Align backup period logs with live status (do not extend stale offline rows)
    sync_backup_log_for_computer(computer)

    # Paginated logs for table
    computer_logs_query = m.BackupLog.query.filter(
        m.BackupLog.computer_id == computer_id,
        m.BackupLog.end_time >= datetime.utcnow() - timedelta(days=90),
    )

    last_log = computer_logs_query.order_by(m.BackupLog.start_time.desc()).first()

    per_page = request.args.get("per_page", 25, type=int)
    pagination = create_pagination(
        total=m.count(computer_logs_query), page_size=per_page
    )

    # Logs object that will be used for table
    logs = (
        db.session.execute(
            computer_logs_query.order_by(m.BackupLog.start_time.desc())
            .limit(pagination.per_page)
            .offset(pagination.skip)
        )
        .scalars()
        .all()
    )

    # Logs information for chart
    chart_days = request.args.get("chart_days", 7, type=int)

    # Prefer computer live status for banner when machine has recent backup/heartbeat
    banner_online_duration_str = None
    if computer.logs_enabled and computer.activated and last_log is not None:
        comp_status = computer.status
        if comp_status in (
            m.ComputerStatus.ONLINE,
            m.ComputerStatus.ONLINE_NO_BACKUP,
        ):
            now_est = CFG.offset_to_est(datetime.utcnow(), True)
            last_activity = computer.last_download_time or computer.last_time_online
            if last_activity:
                delta = now_est - last_activity
                banner_online_duration_str = _format_duration(delta)

    logs_for_chart = (
        m.BackupLog.query.filter(
            m.BackupLog.computer_id == computer_id,
            m.BackupLog.end_time >= datetime.utcnow() - timedelta(days=chart_days),
        )
        .order_by(m.BackupLog.start_time.asc())
        .all()
    )

    # Stale multi-day offline rows make the chart all-red while the device is online
    current_east_time = CFG.offset_to_est(datetime.utcnow(), True)
    if (
        computer.last_download_time
        and computer.last_download_time
        >= current_east_time - timedelta(hours=1, minutes=30)
    ):
        logs_for_chart = [
            log
            for log in logs_for_chart
            if not (
                log.error
                and (log.est_end_time - log.est_start_time) > timedelta(days=1)
            )
        ]

    # List objects with different data for chart.
    labels = []
    notes = []
    chart_green_data = []
    chart_yellow_data = []
    chart_red_data = []

    if logs_for_chart:
        # Set the start time for the chart (eastern time - chart_days)
        log_time = CFG.offset_to_est(
            datetime.utcnow().replace(minute=0, second=0, microsecond=0), True
        ) - timedelta(days=chart_days)
        log_to_use_index = 0

        current_log_type = None

        # Fill all the list objects with data for every hour
        while log_time < CFG.offset_to_est(datetime.utcnow(), True):
            labels.append(log_time)
            notes.append(logs_for_chart[log_to_use_index].notes)

            # Check if the current log_time is in the range of the current log
            if (
                log_time < logs_for_chart[log_to_use_index].est_start_time
                or log_time > logs_for_chart[log_to_use_index].est_end_time
            ):
                current_log_type = None
            elif not logs_for_chart[log_to_use_index].error:
                current_log_type = "green"
            elif (
                logs_for_chart[log_to_use_index].error
                == "Longer than 1 hour without a backup"
            ):
                current_log_type = "yellow"
            else:
                current_log_type = "red"

            chart_green_data.append(1 if current_log_type == "green" else None)
            chart_yellow_data.append(1 if current_log_type == "yellow" else None)
            chart_red_data.append(1 if current_log_type == "red" else None)

            log_time += timedelta(hours=1)

            if (
                log_time > logs_for_chart[log_to_use_index].est_end_time
                and (log_to_use_index + 1) <= len(logs_for_chart) - 1
            ):
                log_to_use_index += 1

    return render_template(
        "info/computer.html",
        computer=computer,
        last_log=last_log,
        banner_online_duration_str=banner_online_duration_str,
        logs=logs,
        page=pagination,
        chart_days=chart_days,
        labels=labels,
        chart_notes=notes,
        chart_green_data=chart_green_data,
        chart_yellow_data=chart_yellow_data,
        chart_red_data=chart_red_data,
        computers_search_params=computers_search_params,
    )


@info_blueprint.route("/system-log", methods=["GET"])
@login_required
def system_log_info():
    # This page is available only for Global admin users
    if (
        current_user.permission != m.UserPermissionLevel.GLOBAL
        or current_user.role != m.UserRole.ADMIN
    ):
        abort(403, "You don't have permission to access this page.")

    LOGS_TYPES = ["All", "Computer", "User", "Company", "Location"]

    logs_type = request.args.get("type", "All", type=str)
    days = request.args.get("days", 30, type=int)
    per_page = request.args.get("per_page", 25, type=int)
    q = request.args.get("q", type=str, default=None)

    system_logs_query = m.SystemLog.query.filter(
        m.SystemLog.created_at >= datetime.utcnow() - timedelta(days=days),
    )

    if logs_type not in LOGS_TYPES:
        logs_type = "All"

    # Filter query by some specific log type
    if logs_type == "All":
        pass
    elif logs_type == "Computer":
        system_logs_query = system_logs_query.filter(
            (m.SystemLog.log_type == m.SystemLogType.COMPUTER_CREATED)
            | (m.SystemLog.log_type == m.SystemLogType.COMPUTER_UPDATED)
            | (m.SystemLog.log_type == m.SystemLogType.COMPUTER_DELETED)
        )
    elif logs_type == "User":
        system_logs_query = system_logs_query.filter(
            (m.SystemLog.log_type == m.SystemLogType.USER_CREATED)
            | (m.SystemLog.log_type == m.SystemLogType.USER_UPDATED)
            | (m.SystemLog.log_type == m.SystemLogType.USER_DELETED)
        )
    elif logs_type == "Company":
        system_logs_query = system_logs_query.filter(
            (m.SystemLog.log_type == m.SystemLogType.COMPANY_CREATED)
            | (m.SystemLog.log_type == m.SystemLogType.COMPANY_UPDATED)
            | (m.SystemLog.log_type == m.SystemLogType.COMPANY_DELETED)
        )
    elif logs_type == "Location":
        system_logs_query = system_logs_query.filter(
            (m.SystemLog.log_type == m.SystemLogType.LOCATION_CREATED)
            | (m.SystemLog.log_type == m.SystemLogType.LOCATION_UPDATED)
            | (m.SystemLog.log_type == m.SystemLogType.LOCATION_DELETED)
        )

    # Filter query by search query
    if q:
        system_logs_query = system_logs_query.join(
            m.SystemLog.created_by, aliased=True
        ).filter(
            (m.SystemLog.object_name.ilike(f"%{q}%"))
            | (m.User.username.ilike(f"%{q}%"))
        )

    pagination = create_pagination(total=m.count(system_logs_query), page_size=per_page)

    system_logs = (
        db.session.execute(
            system_logs_query.order_by(m.SystemLog.created_at.desc())
            .limit(pagination.per_page)
            .offset(pagination.skip)
        )
        .scalars()
        .all()
    )

    pcc_daily_requests_limit: int = int(current_app.config["PCC_DAILY_QUOTA_LIMIT"])
    pcc_daily_requests_count: m.PCCDailyRequest | None = m.PCCDailyRequest.query.filter(
        m.PCCDailyRequest.reset_time >= datetime.utcnow()
    ).first()
    usage_percent = (
        int(pcc_daily_requests_count.requests_count / pcc_daily_requests_limit * 100)
        if pcc_daily_requests_count
        else 0
    )

    return render_template(
        "info/system_log.html",
        system_logs=system_logs,
        page=pagination,
        days=days,
        current_logs_type=logs_type,
        possible_logs_types=LOGS_TYPES,
        daily_limit=pcc_daily_requests_limit,
        current_requests_count=pcc_daily_requests_count,
        usage_percent=usage_percent,
    )


@info_blueprint.route("/location/<int:location_id>", methods=["GET"])
@login_required
def location_info(location_id):
    location = m.Location.query.filter_by(id=location_id).first_or_404()

    # Check if user has access to location information
    if not has_access_to_location(current_user, location):
        abort(403, "You don't have access to this location information.")

    return render_template(
        "info/location.html",
        location=location,
        computers=location.computers,
    )


@info_blueprint.route("/company/<int:company_id>", methods=["GET"])
@login_required
def company_info(company_id):
    company = m.Company.query.filter_by(id=company_id).first_or_404()

    # Check if user has access to company information
    if not has_access_to_company(current_user, company):
        abort(403, "You don't have access to this company information.")

    # get locations online and offline and in percentages

    return render_template(
        "info/company.html",
        company=company,
    )
