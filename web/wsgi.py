#!/usr/bin/env python
from datetime import datetime, timedelta

import click

from app import create_app, db, forms, models
from app.logger import logger

app = create_app()


# flask cli context setup
@app.shell_context_processor
def get_context():
    """Objects exposed here will be automatically available from the shell."""
    return dict(app=app, db=db, m=models, f=forms)


@app.cli.command()
def create_superuser():
    from app.controllers import create_superuser

    create_superuser()


@app.cli.command()
def empty_to_stable():
    from app.controllers import empty_to_stable

    empty_to_stable()


@app.cli.command()
def update_cl_stat():
    from app.controllers import update_companies_locations_statistic

    update_companies_locations_statistic()


@app.cli.command()
def get_pcc_access_key():
    from app.controllers import get_pcc_2_legged_token

    access_key = get_pcc_2_legged_token()
    print(access_key)


@app.cli.command()
def repair_backup_logs():
    """Fix backup period logs for computers with recent downloads (after file_api-only updates)."""
    from datetime import timedelta

    from app.controllers import sync_backup_log_for_computer
    from app.models import Computer
    from config import BaseConfig as CFG

    current_east_time = CFG.offset_to_est(datetime.utcnow(), True)
    cutoff = current_east_time - timedelta(hours=1, minutes=30)
    computers = Computer.query.filter(
        Computer.activated.is_(True),
        Computer.logs_enabled.is_(True),
        Computer.last_download_time.is_not(None),
        Computer.last_download_time >= cutoff,
    ).all()

    print(f"Repairing backup logs for {len(computers)} computers with recent downloads...")
    for computer in computers:
        sync_backup_log_for_computer(computer)
    print("Done.")


@app.cli.command()
def list_online_devices():
    """List all activated computers that are currently online."""
    from app.models import Computer
    from app.models.computer import ComputerStatus

    computers = (
        models.Computer.query.filter(models.Computer.activated.is_(True))
        .order_by(models.Computer.computer_name)
        .all()
    )
    online = [c for c in computers if c.status in (ComputerStatus.ONLINE, ComputerStatus.ONLINE_NO_BACKUP)]

    if not online:
        print("No online devices.")
        return

    print(f"Online devices ({len(online)}):")
    for c in online:
        location = c.location_name or "-"
        company = c.company_name or "-"
        print(f"  {c.computer_name} | {location} | {company} | {c.status.value}")


@app.cli.command()
def clean_old_logs():
    from app.controllers import clean_old_logs

    clean_old_logs()


@app.cli.command()
@click.option("--computer-name", type=str)
@click.option("--days", type=int)
def gen_backup_download_logs(computer_name: str | None = None, days: int | None = None):
    from app.controllers import gen_fake_backup_download_logs

    time_period = timedelta(days=days) if days else timedelta(days=30)

    if computer_name:
        computer = models.Computer.query.filter_by(computer_name=computer_name).first()

        gen_fake_backup_download_logs(computer, time_period)
    else:
        computers = models.Computer.query.all()

        for computer in computers:
            gen_fake_backup_download_logs(computer, time_period)


@app.cli.command()
@click.option("--computer-name", type=str)
@click.option("--days", type=int)
def gen_backup_periods_logs(computer_name: str | None = None, days: int | None = None):
    from app.controllers import gen_fake_backup_periods_logs

    time_period = timedelta(days=days) if days else timedelta(days=30)

    if computer_name:
        computer = models.Computer.query.filter_by(computer_name=computer_name).first()

        gen_fake_backup_periods_logs(computer, time_period)
    else:
        computers = models.Computer.query.all()

        for computer in computers:
            gen_fake_backup_periods_logs(computer, time_period)


@app.cli.command()
@click.argument("scan_record_id", type=int)
def scan_pcc_activations(scan_record_id: int):
    from app.controllers import scan_pcc_activations

    scan_pcc_activations(scan_record_id)


@app.cli.command()
def critical_alert_email():
    from app.controllers import send_critical_alert

    send_critical_alert()


@app.cli.command()
def primary_computer_alert_email():
    from app.controllers import send_primary_computer_alert

    send_primary_computer_alert()


@app.cli.command()
def alternate_computer_alert_email():
    from app.controllers import send_alternate_computer_alert

    send_alternate_computer_alert()


@app.cli.command()
def daily_summary_email():
    from app.controllers import send_daily_summary

    send_daily_summary()


@app.cli.command()
def weekly_summary_email():
    from app.controllers import send_weekly_summary

    send_weekly_summary()


@app.cli.command()
def monthly_email():
    from app.controllers import send_monthly_email

    send_monthly_email()


@app.cli.command()
def set_computer_user_deactivated_at():
    logger.info("<-----Start script------>")
    deactivated_computers = models.Computer.query.filter(
        models.Computer.activated.is_(False)
    ).all()
    deactivated_users = models.User.query.filter(models.User.activated.is_(False)).all()

    for computer in deactivated_computers:
        computer.deactivated_at = datetime.utcnow()

    for user in deactivated_users:
        user.deactivated_at = datetime.utcnow()

    db.session.commit()
    logger.info("<-----End script------>")


if __name__ == "__main__":
    app.run()
