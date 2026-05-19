import enum
import random
import zoneinfo
from datetime import datetime, timedelta

from app import models as m
from app.logger import logger
from config import BaseConfig as CFG


class BackupLogError(enum.Enum):
    ONE_HOUR = "Longer than 1 hour without a backup"
    TWO_HOURS = "Longer than 2 hours without a backup"


def _cap_stale_offline_log_start(
    offline_log: m.BackupLog, rounded_current_time: datetime
) -> None:
    """Avoid multi-month offline rows when closing a stale NO_DOWNLOADS_PERIOD log."""
    max_offline_span = timedelta(days=1)
    if rounded_current_time - offline_log.start_time > max_offline_span:
        offline_log.start_time = rounded_current_time - max_offline_span


def sync_backup_log_for_computer(computer: m.Computer) -> None:
    """Update backup period logs to match live computer timestamps."""
    if not computer.logs_enabled:
        return

    current_east_time = CFG.offset_to_est(datetime.utcnow(), True)
    if (
        computer.last_download_time
        and computer.last_download_time
        >= current_east_time - timedelta(hours=1, minutes=30)
    ):
        utc_download_time = (
            computer.last_download_time.replace(
                tzinfo=zoneinfo.ZoneInfo("America/New_York")
            )
            .astimezone(zoneinfo.ZoneInfo("UTC"))
            .replace(tzinfo=None)
        )
        backup_log_on_download_success(computer, utc_download_time)
    else:
        backup_log_on_request_to_view(computer)


def backup_log_on_download_success(
    computer: m.Computer,
    current_time: datetime = datetime.utcnow(),
):
    """Create or update backup log for computer

    Args:
        computer (m.Computer): Computer object
        current_time (datetime, optional): time when log should be updated or created.Should be in UTC not EST.
        Defaults to datetime.utcnow().
    """
    last_computer_log = (
        m.BackupLog.query.filter_by(computer_id=computer.id)
        .order_by(m.BackupLog.start_time.desc(), m.BackupLog.end_time.desc())
        .first()
    )

    # Remove minutes, seconds and microseconds from current time
    rounded_current_time = current_time.replace(minute=0, second=0, microsecond=0)

    # If there is no backup log for this computer - create the first one
    if not last_computer_log:
        new_with_downloads_log = m.BackupLog(
            backup_log_type=m.BackupLogType.WITH_DOWNLOADS_PERIOD,
            start_time=rounded_current_time,
            end_time=rounded_current_time + timedelta(hours=1) - timedelta(seconds=1),
            computer_id=computer.id,
        )
        new_with_downloads_log.save()
        logger.debug(
            "Log on_download 1: Created first backup log (successful) for computer {}",
            computer.computer_name,
        )
    else:
        # If the last backup log has type WITH_DOWNLOADS_PERIOD
        if last_computer_log.backup_log_type == m.BackupLogType.WITH_DOWNLOADS_PERIOD:
            # If the last backup log end time is less than 1 hour ago - update it
            if last_computer_log.end_time + timedelta(hours=1) > rounded_current_time:
                last_computer_log.end_time = (
                    rounded_current_time + timedelta(hours=1) - timedelta(seconds=1)
                )
                last_computer_log.update()

                logger.debug(
                    "Log on_download 2: Updated WITH_DOWNLOADS_LOG log for computer {}. Log ID: {}",
                    computer.computer_name,
                    last_computer_log.id,
                )
            # If the last backup log end time is more than 1 hour ago
            # Create new backup log\logs with type NO_DOWNLOADS_PERIOD (if logs were enabled)
            # and then WITH_DOWNLOADS_PERIOD
            else:
                # If all the time logs were enabled - computer was offline in that time
                if (
                    computer.last_time_logs_enabled
                    <= last_computer_log.end_time + timedelta(seconds=1)
                ):
                    new_no_downloads_log = m.BackupLog(
                        backup_log_type=m.BackupLogType.NO_DOWNLOADS_PERIOD,
                        start_time=last_computer_log.end_time + timedelta(seconds=1),
                        end_time=rounded_current_time - timedelta(seconds=1),
                        computer_id=computer.id,
                        notes="Device is offline",
                    )
                    new_no_downloads_log.error = (
                        BackupLogError.TWO_HOURS.value
                        if new_no_downloads_log.duration
                        > timedelta(minutes=59, seconds=59)
                        else BackupLogError.ONE_HOUR.value
                    )
                    new_no_downloads_log.save()

                    logger.debug(
                        "Log on_download 3: Created new NO_DOWNLOADS_PERIOD log for computer {}. Log ID: {}",
                        computer.computer_name,
                        new_no_downloads_log.id,
                    )

                # If logs were disabled for some time and then enabled
                elif (
                    computer.last_time_logs_enabled
                    > last_computer_log.end_time + timedelta(seconds=1)
                    and computer.last_time_logs_enabled < rounded_current_time
                ):
                    # Check if there should be one or two NO_DOWNLOADS_PERIOD logs because of enabled\disabled
                    if computer.last_time_logs_disabled.replace(
                        minute=0, second=0, microsecond=0
                    ) == computer.last_time_logs_enabled.replace(
                        minute=0, second=0, microsecond=0
                    ):
                        new_no_downloads_log = m.BackupLog(
                            backup_log_type=m.BackupLogType.NO_DOWNLOADS_PERIOD,
                            start_time=computer.last_time_logs_enabled.replace(
                                minute=0, second=0, microsecond=0
                            ),
                            end_time=rounded_current_time - timedelta(seconds=1),
                            computer_id=computer.id,
                            notes="Device is offline",
                        )
                        new_no_downloads_log.error = (
                            BackupLogError.TWO_HOURS.value
                            if new_no_downloads_log.duration
                            > timedelta(minutes=59, seconds=59)
                            else BackupLogError.ONE_HOUR.value
                        )
                        new_no_downloads_log.save()

                        logger.debug(
                            "Log on_download 4: Created new NO_DOWNLOADS_PERIOD log for computer {}. Log ID: {}",
                            computer.computer_name,
                            new_no_downloads_log.id,
                        )
                    else:
                        first_no_downloads_log = m.BackupLog(
                            backup_log_type=m.BackupLogType.NO_DOWNLOADS_PERIOD,
                            start_time=last_computer_log.end_time
                            + timedelta(seconds=1),
                            end_time=computer.last_time_logs_disabled.replace(
                                minute=0, second=0, microsecond=0
                            )
                            - timedelta(seconds=1),
                            computer_id=computer.id,
                            notes="Device is offline",
                        )
                        first_no_downloads_log.error = (
                            BackupLogError.TWO_HOURS.value
                            if first_no_downloads_log.duration
                            > timedelta(minutes=59, seconds=59)
                            else BackupLogError.ONE_HOUR.value
                        )
                        first_no_downloads_log.save()

                        second_no_downloads_log = m.BackupLog(
                            backup_log_type=m.BackupLogType.NO_DOWNLOADS_PERIOD,
                            start_time=computer.last_time_logs_enabled.replace(
                                minute=0, second=0, microsecond=0
                            ),
                            end_time=rounded_current_time - timedelta(seconds=1),
                            computer_id=computer.id,
                            notes="Device is offline",
                        )
                        second_no_downloads_log.error = (
                            BackupLogError.TWO_HOURS.value
                            if second_no_downloads_log.duration
                            > timedelta(minutes=59, seconds=59)
                            else BackupLogError.ONE_HOUR.value
                        )
                        second_no_downloads_log.save()
                        logger.debug(
                            "Log on_download 5: Created two new NO_DOWNLOADS_PERIOD \
                                log for computer {}. Logs IDs: {} and {}",
                            computer.computer_name,
                            first_no_downloads_log.id,
                            second_no_downloads_log.id,
                        )
                else:
                    # If logs were just enabled and there was no NO_DOWNLOADS_PERIOD
                    pass

                new_with_downloads_log = m.BackupLog(
                    backup_log_type=m.BackupLogType.WITH_DOWNLOADS_PERIOD,
                    start_time=rounded_current_time,
                    end_time=rounded_current_time + timedelta(minutes=59, seconds=59),
                    computer_id=computer.id,
                )
                new_with_downloads_log.save()

                logger.debug(
                    "Log on_download 6: Created new WITH_DOWNLOADS_PERIOD log for computer {}. Log ID: {}",
                    computer.computer_name,
                    new_with_downloads_log.id,
                )

        # If the last backup log has type NO_DOWNLOADS_PERIOD
        elif last_computer_log.backup_log_type == m.BackupLogType.NO_DOWNLOADS_PERIOD:
            # Set end time for the last backup log and create new backup log with type WITH_DOWNLOADS_PERIOD

            # If all the time logs were enabled - computer was offline in that time

            if computer.last_time_logs_enabled <= last_computer_log.end_time:
                if last_computer_log.start_time < rounded_current_time:
                    _cap_stale_offline_log_start(
                        last_computer_log, rounded_current_time
                    )
                    last_computer_log.end_time = rounded_current_time - timedelta(
                        seconds=1
                    )

                    # Update error field if necessary
                    last_computer_log.error = (
                        BackupLogError.TWO_HOURS.value
                        if last_computer_log.duration
                        > timedelta(minutes=59, seconds=59)
                        else BackupLogError.ONE_HOUR.value
                    )
                # When computer just was activated, user looked at the logs (NO_DOWLOADS log was created)
                # And then computer downloaded backup in the same hour
                else:
                    last_computer_log.end_time = rounded_current_time + timedelta(
                        minutes=59, seconds=59
                    )
                    last_computer_log.backup_log_type = (
                        m.BackupLogType.WITH_DOWNLOADS_PERIOD
                    )
                    last_computer_log.error = ""
                    last_computer_log.notes = ""

                last_computer_log.update()

                logger.debug(
                    "Log on_download 7: Updated NO_DOWNLOADS_PERIOD log for computer {}. Log ID: {}",
                    computer.computer_name,
                    last_computer_log.id,
                )

            # if logs were disabled after some time and then enabled
            elif (
                computer.last_time_logs_enabled > last_computer_log.end_time
                and computer.last_time_logs_enabled < rounded_current_time
            ):
                last_computer_log.end_time = computer.last_time_logs_disabled.replace(
                    minute=0, second=0, microsecond=0
                ) - timedelta(seconds=1)

                last_computer_log.error = (
                    BackupLogError.TWO_HOURS.value
                    if last_computer_log.duration > timedelta(minutes=59, seconds=59)
                    else BackupLogError.ONE_HOUR.value
                )
                last_computer_log.update()

                new_no_downloads_log = m.BackupLog(
                    backup_log_type=m.BackupLogType.NO_DOWNLOADS_PERIOD,
                    start_time=computer.last_time_logs_enabled.replace(
                        minute=0, second=0, microsecond=0
                    ),
                    end_time=rounded_current_time - timedelta(seconds=1),
                    computer_id=computer.id,
                    notes="Device is offline",
                )
                new_no_downloads_log.error = (
                    BackupLogError.TWO_HOURS.value
                    if new_no_downloads_log.duration > timedelta(minutes=59, seconds=59)
                    else BackupLogError.ONE_HOUR.value
                )
                new_no_downloads_log.save()

                logger.debug(
                    "Log on_download 8: Updated NO_DOWNLOADS_PERIOD and created \
                        new NO_DOWNLOADS_PERIOD logs for computer {}. Logs IDs: {} and {}",
                    computer.computer_name,
                    last_computer_log.id,
                    new_no_downloads_log.id,
                )

            # if logs were just enabled
            else:
                last_computer_log.end_time = computer.last_time_logs_disabled.replace(
                    minute=0, second=0, microsecond=0
                ) - timedelta(seconds=1)

                last_computer_log.error = (
                    BackupLogError.TWO_HOURS.value
                    if last_computer_log.duration > timedelta(minutes=59, seconds=59)
                    else BackupLogError.ONE_HOUR.value
                )
                last_computer_log.update()
                logger.debug(
                    "Log on_download 9: Updated NO_DOWNLOADS_PERIOD log for computer {}. Log ID: {}",
                    computer.computer_name,
                    last_computer_log.id,
                )

            new_with_downloads_log = m.BackupLog(
                backup_log_type=m.BackupLogType.WITH_DOWNLOADS_PERIOD,
                start_time=rounded_current_time,
                end_time=rounded_current_time + timedelta(minutes=59, seconds=59),
                computer_id=computer.id,
            )
            new_with_downloads_log.save()

            logger.debug(
                "Log on_download 10: Created new WITH_DOWNLOADS_PERIOD log for computer {}. Log ID: {}",
                computer.computer_name,
                new_with_downloads_log.id,
            )


def gen_fake_backup_periods_logs(computer: m.Computer, time_period: timedelta):
    """Generate fake logs about periods with and without backups downloads for computer

    Args:
        computer (m.Computer): Computer object
        time_period (timedelta): Time period for logs
    """
    # Set up list of working hours
    WORKING_HOURS = tuple(range(8, 13)) + tuple(range(14, 23))

    # Check if there are any logs exist for this computer
    current_logs = m.BackupLog.query.filter_by(computer_id=computer.id).all()
    if current_logs:
        logger.info("Backup logs already exist for computer {}", computer)
        return

    log_time = (datetime.utcnow() - time_period).replace(
        minute=0, second=0, microsecond=0
    )

    for hour in range(time_period.days * 24 + time_period.seconds // 3600):
        random_number = random.randint(1, 10)
        random_waiting = timedelta(minutes=random.randint(0, 30))
        if log_time.hour in WORKING_HOURS and random_number != 10:
            backup_log_on_download_success(computer, log_time + random_waiting)

        log_time += timedelta(hours=1)

    logger.info(
        "<-----Fake backup periods logs were generated for computer {}----->",
        computer,
    )


def backup_log_on_request_to_view(computer: m.Computer):
    """Create or update the last computer backup log on request to view.

    Args:
        computer (m.Computer): Computer object
    """
    last_computer_log = (
        m.BackupLog.query.filter_by(computer_id=computer.id)
        .order_by(m.BackupLog.start_time.desc())
        .first()
    )

    # Current time in UTC
    current_time = datetime.utcnow()

    # Current time in UTC rounded to hours
    rounded_current_time = datetime.utcnow().replace(minute=0, second=0, microsecond=0)

    # If there is no backup logs for this computer but computer activated - create new one with type NO_DOWNLOADS_PERIOD
    if not last_computer_log:
        # If not activated - do nothing
        if not computer.activated:
            logger.debug(
                "Log on_request 1: pass backup period logs creation: computer {} not activated",
                computer.computer_name,
            )
            return

        new_no_downloads_log = m.BackupLog(
            backup_log_type=m.BackupLogType.NO_DOWNLOADS_PERIOD,
            start_time=computer.last_time_logs_enabled.replace(
                minute=0, second=0, microsecond=0
            ),
            end_time=rounded_current_time + timedelta(minutes=59, seconds=59),
            computer_id=computer.id,
            notes="Device is offline",
        )
        new_no_downloads_log.error = (
            BackupLogError.TWO_HOURS.value
            if new_no_downloads_log.duration > timedelta(minutes=59, seconds=59)
            else BackupLogError.ONE_HOUR.value
        )
        new_no_downloads_log.save()
        logger.debug(
            "Log on_request 2: Created first backup log (unsuccessful) for computer {}",
            computer.computer_name,
        )

        return

    # If the last backup log has type WITH_DOWNLOADS_PERIOD
    if last_computer_log.backup_log_type == m.BackupLogType.WITH_DOWNLOADS_PERIOD:
        # If the last backup log end time + 30 minutes and 1 second is bigger than current time - ignore it
        if last_computer_log.end_time + timedelta(minutes=30, seconds=1) > current_time:
            return

        # If the last backup log end time + 30 minutes and 1 second is less than current_time
        # Create new backup log with type NO_DOWNLOADS_PERIOD
        else:
            # If logs were enabled all the time
            if computer.last_time_logs_enabled <= last_computer_log.end_time:
                new_no_downloads_log = m.BackupLog(
                    backup_log_type=m.BackupLogType.NO_DOWNLOADS_PERIOD,
                    start_time=last_computer_log.end_time + timedelta(seconds=1),
                    end_time=rounded_current_time + timedelta(minutes=59, seconds=59),
                    computer_id=computer.id,
                    notes="Device is offline",
                )

                new_no_downloads_log.error = (
                    BackupLogError.TWO_HOURS.value
                    if new_no_downloads_log.duration > timedelta(minutes=59, seconds=59)
                    else BackupLogError.ONE_HOUR.value
                )
                new_no_downloads_log.save()

                logger.debug(
                    "Log on_request 3: Created new NO_DOWNLOADS_PERIOD log for computer {}. Log ID: {}",
                    computer.computer_name,
                    new_no_downloads_log.id,
                )
            # If logs were disabled for some time and then enabled
            elif computer.last_time_logs_enabled > last_computer_log.end_time:
                # If logs were disabled just after the creation of the last backup log
                if computer.last_time_logs_disabled.replace(
                    minute=0, second=0, microsecond=0
                ) == last_computer_log.end_time + timedelta(seconds=1):
                    new_no_downloads_log = m.BackupLog(
                        backup_log_type=m.BackupLogType.NO_DOWNLOADS_PERIOD,
                        start_time=computer.last_time_logs_enabled.replace(
                            minute=0, second=0, microsecond=0
                        ),
                        end_time=rounded_current_time
                        + timedelta(minutes=59, seconds=59),
                        computer_id=computer.id,
                        notes="Device is offline",
                    )

                    new_no_downloads_log.error = (
                        BackupLogError.TWO_HOURS.value
                        if new_no_downloads_log.duration
                        > timedelta(minutes=59, seconds=59)
                        else BackupLogError.ONE_HOUR.value
                    )
                    new_no_downloads_log.save()
                    logger.debug(
                        "Log on_request 4: Created new NO_DOWNLOADS_PERIOD log for computer {}. Log ID: {}",
                        computer.computer_name,
                        new_no_downloads_log.id,
                    )
                # In all other cases there should be two NO_DOWNLOADS_PERIOD logs (before disabled and after enabled)
                else:
                    first_no_downloads_log = m.BackupLog(
                        backup_log_type=m.BackupLogType.NO_DOWNLOADS_PERIOD,
                        start_time=last_computer_log.end_time + timedelta(seconds=1),
                        end_time=computer.last_time_logs_disabled.replace(
                            minute=0, second=0, microsecond=0
                        )
                        - timedelta(seconds=1),
                        computer_id=computer.id,
                        notes="Device is offline",
                    )

                    first_no_downloads_log.error = (
                        BackupLogError.TWO_HOURS.value
                        if first_no_downloads_log.duration
                        > timedelta(minutes=59, seconds=59)
                        else BackupLogError.ONE_HOUR.value
                    )
                    first_no_downloads_log.save()

                    second_no_downloads_log = m.BackupLog(
                        backup_log_type=m.BackupLogType.NO_DOWNLOADS_PERIOD,
                        start_time=computer.last_time_logs_enabled.replace(
                            minute=0, second=0, microsecond=0
                        ),
                        end_time=rounded_current_time
                        + timedelta(minutes=59, seconds=59),
                        computer_id=computer.id,
                        notes="Device is offline",
                    )

                    second_no_downloads_log.error = (
                        BackupLogError.TWO_HOURS.value
                        if second_no_downloads_log.duration
                        > timedelta(minutes=59, seconds=59)
                        else BackupLogError.ONE_HOUR.value
                    )
                    second_no_downloads_log.save()

                    logger.debug(
                        "Log on_request 5: Created two new \
                            NO_DOWNLOADS_PERIOD logs for computer {}. Logs IDs: {} and {}",
                        computer.computer_name,
                        first_no_downloads_log.id,
                        second_no_downloads_log.id,
                    )

    # If the last backup log has type NO_DOWNLOADS_PERIOD
    elif last_computer_log.backup_log_type == m.BackupLogType.NO_DOWNLOADS_PERIOD:
        # Update the end time for the last backup log if logs were enabled all the time
        if computer.last_time_logs_enabled < last_computer_log.end_time:
            last_computer_log.end_time = rounded_current_time + timedelta(
                minutes=59, seconds=59
            )

            # Update error field if necessary
            last_computer_log.error = (
                BackupLogError.TWO_HOURS.value
                if last_computer_log.duration > timedelta(minutes=59, seconds=59)
                else BackupLogError.ONE_HOUR.value
            )

            last_computer_log.update()

            logger.debug(
                "Log on_request 6: Updated NO_DOWNLOADS_PERIOD log for computer {}. Log IDs: {}",
                computer.computer_name,
                last_computer_log.id,
            )

        # If logs were disabled for some time and now they are enabled
        elif computer.last_time_logs_enabled > last_computer_log.end_time:
            last_computer_log.end_time = computer.last_time_logs_disabled.replace(
                minute=0, second=0, microsecond=0
            ) - timedelta(seconds=1)

            # Update error field if necessary
            last_computer_log.error = (
                BackupLogError.TWO_HOURS.value
                if last_computer_log.duration > timedelta(minutes=59, seconds=59)
                else BackupLogError.ONE_HOUR.value
            )
            last_computer_log.update()

            new_no_downloads_log = m.BackupLog(
                backup_log_type=m.BackupLogType.NO_DOWNLOADS_PERIOD,
                start_time=computer.last_time_logs_enabled.replace(
                    minute=0, second=0, microsecond=0
                ),
                end_time=rounded_current_time + timedelta(minutes=59, seconds=59),
                computer_id=computer.id,
                notes="Device is offline",
            )

            new_no_downloads_log.error = (
                BackupLogError.TWO_HOURS.value
                if new_no_downloads_log.duration > timedelta(minutes=59, seconds=59)
                else BackupLogError.ONE_HOUR.value
            )
            new_no_downloads_log.save()

            logger.debug(
                "Log on_request 7: updated and created NO_DOWNLOADS_PERIOD logs for computer {}. Logs IDss: {} and {}",
                computer.computer_name,
                last_computer_log.id,
                new_no_downloads_log.id,
            )


def backup_log_on_download_error(computer: m.Computer):
    """Create or update the last computer backup log on downloading error.

    Args:
        computer (m.Computer): Computer object
    """
    last_computer_log = (
        m.BackupLog.query.filter_by(computer_id=computer.id)
        .order_by(m.BackupLog.start_time.desc())
        .first()
    )

    # Current time in UTC rounded to hours
    rounded_current_time = datetime.utcnow().replace(minute=0, second=0, microsecond=0)

    # If there is no backup log for this computer - create new one with type NO_DOWNLOADS_PERIOD
    if not last_computer_log:
        new_no_downloads_log = m.BackupLog(
            backup_log_type=m.BackupLogType.NO_DOWNLOADS_PERIOD,
            start_time=rounded_current_time,
            end_time=rounded_current_time + timedelta(minutes=59, seconds=59),
            computer_id=computer.id,
            error=BackupLogError.ONE_HOUR.value,
            notes="Unsuccessful backup",
        )
        new_no_downloads_log.save()
        logger.debug(
            "Log on_download_error 1: Created first backup log (unsuccessful) for computer {}",
            computer.computer_name,
        )

    else:
        # If the last backup log has type WITH_DOWNLOADS_PERIOD
        if last_computer_log.backup_log_type == m.BackupLogType.WITH_DOWNLOADS_PERIOD:
            # Create new backup log with type NO_DOWNLOADS_PERIOD

            # Check the logs were enabled all the time
            if computer.last_time_logs_enabled <= last_computer_log.end_time:
                new_no_downloads_log = m.BackupLog(
                    backup_log_type=m.BackupLogType.NO_DOWNLOADS_PERIOD,
                    start_time=last_computer_log.end_time + timedelta(seconds=1),
                    end_time=rounded_current_time + timedelta(minutes=59, seconds=59),
                    computer_id=computer.id,
                    notes="Unsuccessful backup",
                )
                new_no_downloads_log.error = (
                    BackupLogError.TWO_HOURS.value
                    if new_no_downloads_log.duration > timedelta(minutes=59, seconds=59)
                    else BackupLogError.ONE_HOUR.value
                )
                new_no_downloads_log.save()
                logger.debug(
                    "Log on_download_error 2: Created new NO_DOWNLOADS_PERIOD log for computer {}. Log ID: {}",
                    computer.computer_name,
                    new_no_downloads_log.id,
                )
            # If logs were disabled for some time and then enabled
            elif computer.last_time_logs_enabled > last_computer_log.end_time:
                # If logs were disabled just after the creation of the last backup log
                if computer.last_time_logs_disabled.replace(
                    minute=0, second=0, microsecond=0
                ) == last_computer_log.end_time + timedelta(seconds=1):
                    new_no_downloads_log = m.BackupLog(
                        backup_log_type=m.BackupLogType.NO_DOWNLOADS_PERIOD,
                        start_time=computer.last_time_logs_enabled.replace(
                            minute=0, second=0, microsecond=0
                        ),
                        end_time=rounded_current_time
                        + timedelta(minutes=59, seconds=59),
                        computer_id=computer.id,
                        notes="Unsuccessful backup",
                    )
                    new_no_downloads_log.error = (
                        BackupLogError.TWO_HOURS.value
                        if new_no_downloads_log.duration
                        > timedelta(minutes=59, seconds=59)
                        else BackupLogError.ONE_HOUR.value
                    )
                    new_no_downloads_log.save()
                    logger.debug(
                        "Log on_download_error 3: Created new NO_DOWNLOADS_PERIOD log for computer {}. Log ID: {}",
                        computer.computer_name,
                        new_no_downloads_log.id,
                    )
                # In all other cases there should be two NO_DOWNLOADS_PERIOD logs (before disabled and after enabled)
                else:
                    first_no_downloads_log = m.BackupLog(
                        backup_log_type=m.BackupLogType.NO_DOWNLOADS_PERIOD,
                        start_time=last_computer_log.end_time + timedelta(seconds=1),
                        end_time=computer.last_time_logs_disabled.replace(
                            minute=0, second=0, microsecond=0
                        )
                        - timedelta(seconds=1),
                        computer_id=computer.id,
                        notes="Device is offline",
                    )
                    first_no_downloads_log.error = (
                        BackupLogError.TWO_HOURS.value
                        if first_no_downloads_log.duration
                        > timedelta(minutes=59, seconds=59)
                        else BackupLogError.ONE_HOUR.value
                    )
                    first_no_downloads_log.save()

                    second_no_downloads_log = m.BackupLog(
                        backup_log_type=m.BackupLogType.NO_DOWNLOADS_PERIOD,
                        start_time=computer.last_time_logs_enabled.replace(
                            minute=0, second=0, microsecond=0
                        ),
                        end_time=rounded_current_time
                        + timedelta(minutes=59, seconds=59),
                        computer_id=computer.id,
                        notes="Unsuccessful backup",
                    )
                    second_no_downloads_log.error = (
                        BackupLogError.TWO_HOURS.value
                        if second_no_downloads_log.duration
                        > timedelta(minutes=59, seconds=59)
                        else BackupLogError.ONE_HOUR.value
                    )
                    second_no_downloads_log.save()
                    logger.debug(
                        "Log on_download_error 4: Created 2 new \
                            NO_DOWNLOADS_PERIOD logs for computer {}. Logs IDs: {} and {}",
                        computer.computer_name,
                        first_no_downloads_log.id,
                        second_no_downloads_log.id,
                    )

        # If the last backup log has type NO_DOWNLOADS_PERIOD - update it
        elif last_computer_log.backup_log_type == m.BackupLogType.NO_DOWNLOADS_PERIOD:
            if computer.last_time_logs_enabled < last_computer_log.end_time:
                last_computer_log.end_time = (
                    rounded_current_time + timedelta(hours=1) - timedelta(seconds=1)
                )
                last_computer_log.error = (
                    BackupLogError.TWO_HOURS.value
                    if last_computer_log.duration > timedelta(minutes=59, seconds=59)
                    else BackupLogError.ONE_HOUR.value
                )
                last_computer_log.notes = "Unsuccessful backup"
                last_computer_log.update()
                logger.debug(
                    "Log on_download_error 5: updated NO_DOWNLOADS_PERIOD log for computer {}. Log ID: {}",
                    computer.computer_name,
                    last_computer_log.id,
                )
            else:
                last_computer_log.end_time = computer.last_time_logs_disabled.replace(
                    minute=0, second=0, microsecond=0
                ) - timedelta(seconds=1)
                last_computer_log.error = (
                    BackupLogError.TWO_HOURS.value
                    if last_computer_log.duration > timedelta(minutes=59, seconds=59)
                    else BackupLogError.ONE_HOUR.value
                )
                last_computer_log.update()

                new_no_downloads_log = m.BackupLog(
                    backup_log_type=m.BackupLogType.NO_DOWNLOADS_PERIOD,
                    start_time=computer.last_time_logs_enabled.replace(
                        minute=0, second=0, microsecond=0
                    ),
                    end_time=rounded_current_time + timedelta(minutes=59, seconds=59),
                    computer_id=computer.id,
                    notes="Unsuccessful backup",
                )
                new_no_downloads_log.error = (
                    BackupLogError.TWO_HOURS.value
                    if new_no_downloads_log.duration > timedelta(minutes=59, seconds=59)
                    else BackupLogError.ONE_HOUR.value
                )
                new_no_downloads_log.save()
                logger.debug(
                    "Log on_download_error 6: updated and created new \
                        NO_DOWNLOADS_PERIOD logs for computer {}. Logs IDs: {} and {}",
                    computer.computer_name,
                    last_computer_log.id,
                    new_no_downloads_log.id,
                )


def backup_log_on_download_error_with_message(computer: m.Computer, message: str):
    # Current time in UTC rounded to hours
    rounded_current_time = datetime.utcnow().replace(minute=0, second=0, microsecond=0)
    new_no_downloads_log = m.BackupLog(
        backup_log_type=m.BackupLogType.NO_DOWNLOADS_PERIOD,
        start_time=rounded_current_time,
        end_time=rounded_current_time + timedelta(minutes=59, seconds=59),
        computer_id=computer.id,
        error=BackupLogError.ONE_HOUR.value,
        notes=message,
    )
    new_no_downloads_log.save()
    logger.debug(
        "Log on_download_error 1: Created first backup log (unsuccessful) for computer {}",
        computer.computer_name,
    )
