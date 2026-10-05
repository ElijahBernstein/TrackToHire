

# backend/gmail_worker.py
# Processes durable Gmail jobs outside the FastAPI request process.

import logging
import os
import sys
import time
from datetime import datetime, timedelta, timezone

from dotenv import load_dotenv

import models
from database import SessionLocal
from gmail_service import (
    get_gmail_history_previews,
    start_gmail_watch,
)
from main import process_gmail_messages


load_dotenv()


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)

logger = logging.getLogger("gmail_worker")


POLL_INTERVAL_SECONDS = max(
    1,
    int(os.getenv("GMAIL_WORKER_POLL_SECONDS", "5")),
)

MAX_ATTEMPTS = max(
    1,
    int(os.getenv("GMAIL_WORKER_MAX_ATTEMPTS", "3")),
)

# Check for expiring Gmail watches once per hour. A watch is renewed
# when it has less than 24 hours remaining.
WATCH_RENEWAL_CHECK_SECONDS = max(
    60,
    int(
        os.getenv(
            "GMAIL_WATCH_RENEWAL_CHECK_SECONDS",
            "3600",
        )
    ),
)

WATCH_RENEWAL_LEAD_SECONDS = max(
    3600,
    int(
        os.getenv(
            "GMAIL_WATCH_RENEWAL_LEAD_SECONDS",
            "86400",
        )
    ),
)


def renew_gmail_watches(force: bool = False) -> dict:
    """
    Renews enabled Gmail watches that are close to expiration.

    The saved Gmail history ID is intentionally preserved. It is the
    worker's processing checkpoint, and replacing it during renewal
    could skip messages that have not been processed yet.
    """

    db = SessionLocal()

    try:
        connection_ids = [
            connection_id
            for (connection_id,) in (
                db.query(models.DBGmailConnection.id)
                .filter(
                    models.DBGmailConnection
                    .automatic_monitoring_enabled
                    .is_(True)
                )
                .all()
            )
        ]
    finally:
        db.close()

    result = {
        "enabled_connections": len(connection_ids),
        "renewed": 0,
        "failed": 0,
    }

    renewal_deadline = (
        datetime.now(timezone.utc)
        + timedelta(seconds=WATCH_RENEWAL_LEAD_SECONDS)
    )

    for connection_id in connection_ids:
        connection_db = SessionLocal()

        try:
            gmail_connection = (
                connection_db.query(models.DBGmailConnection)
                .filter(
                    models.DBGmailConnection.id
                    == connection_id,
                    models.DBGmailConnection
                    .automatic_monitoring_enabled
                    .is_(True),
                )
                .first()
            )

            if not gmail_connection:
                continue

            watch_expiration = gmail_connection.watch_expiration

            if (
                watch_expiration is not None
                and watch_expiration.tzinfo is None
            ):
                watch_expiration = watch_expiration.replace(
                    tzinfo=timezone.utc
                )

            renewal_is_due = bool(
                force
                or watch_expiration is None
                or watch_expiration <= renewal_deadline
            )

            if not renewal_is_due:
                continue

            watch_result = start_gmail_watch(
                gmail_connection=gmail_connection
            )

            expiration_milliseconds = int(
                watch_result["expiration"]
            )

            gmail_connection.watch_expiration = (
                datetime.fromtimestamp(
                    expiration_milliseconds / 1000,
                    tz=timezone.utc,
                )
            )

            # Initialize only if a legacy connection has no checkpoint.
            # Never replace an existing processing checkpoint here.
            if not gmail_connection.gmail_history_id:
                gmail_connection.gmail_history_id = (
                    watch_result["history_id"]
                )

            connection_db.commit()
            result["renewed"] += 1

            logger.info(
                "Renewed Gmail watch for connection %s until %s.",
                connection_id,
                gmail_connection.watch_expiration.isoformat(),
            )
        except Exception as error:
            connection_db.rollback()
            result["failed"] += 1

            logger.error(
                "Could not renew Gmail watch for connection %s: %s",
                connection_id,
                error,
            )
        finally:
            connection_db.close()

    return result


def recover_interrupted_jobs() -> int:
    """
    Returns jobs left in processing to pending after a worker restart.

    Run only one worker instance during this project phase.
    """

    db = SessionLocal()

    try:
        recovered_count = (
            db.query(models.DBGmailProcessingJob)
            .filter(
                models.DBGmailProcessingJob.status
                == "processing"
            )
            .update(
                {
                    models.DBGmailProcessingJob.status:
                    "pending",
                },
                synchronize_session=False,
            )
        )

        db.commit()
        return recovered_count
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def claim_next_job() -> int | None:
    """
    Atomically claims the oldest pending job.

    SKIP LOCKED prevents two worker transactions from claiming the
    same row if worker concurrency is added later.
    """

    db = SessionLocal()

    try:
        job = (
            db.query(models.DBGmailProcessingJob)
            .filter(
                models.DBGmailProcessingJob.status
                == "pending"
            )
            .order_by(
                models.DBGmailProcessingJob.created_at.asc(),
                models.DBGmailProcessingJob.id.asc(),
            )
            .with_for_update(skip_locked=True)
            .first()
        )

        if not job:
            db.rollback()
            return None

        job.status = "processing"
        job.attempt_count += 1
        job_id = job.id

        db.commit()
        return job_id
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def mark_job_failed(job_id: int, error: Exception) -> None:
    """
    Requeues a failed job until its maximum attempt count is reached.
    """

    db = SessionLocal()

    try:
        job = (
            db.query(models.DBGmailProcessingJob)
            .filter(
                models.DBGmailProcessingJob.id == job_id
            )
            .first()
        )

        if not job:
            return

        if job.attempt_count >= MAX_ATTEMPTS:
            job.status = "failed"
            job.processed_at = datetime.now(timezone.utc)
        else:
            job.status = "pending"

        db.commit()

        logger.error(
            "Job %s failed on attempt %s/%s: %s",
            job_id,
            job.attempt_count,
            MAX_ATTEMPTS,
            error,
        )
    except Exception:
        db.rollback()
        logger.exception(
            "Could not save failure state for job %s.",
            job_id,
        )
    finally:
        db.close()


def process_job(job_id: int) -> dict:
    """
    Processes one claimed job and atomically saves its final state.
    """

    db = SessionLocal()

    try:
        job = (
            db.query(models.DBGmailProcessingJob)
            .filter(
                models.DBGmailProcessingJob.id == job_id,
                models.DBGmailProcessingJob.status
                == "processing",
            )
            .first()
        )

        if not job:
            return {
                "result": "skipped",
                "reason": "Job is no longer processing.",
            }

        gmail_connection = (
            db.query(models.DBGmailConnection)
            .filter(
                models.DBGmailConnection.id
                == job.gmail_connection_id
            )
            .first()
        )

        if (
            not gmail_connection
            or not gmail_connection.automatic_monitoring_enabled
        ):
            job.status = "completed"
            job.processed_at = datetime.now(timezone.utc)
            db.commit()

            return {
                "result": "skipped",
                "reason": "Gmail monitoring is no longer enabled.",
            }

        start_history_id = gmail_connection.gmail_history_id

        if not start_history_id:
            raise RuntimeError(
                "The Gmail connection has no saved history ID."
            )

        # A prior job may already have advanced beyond this notification.
        if int(job.history_id) <= int(start_history_id):
            job.status = "completed"
            job.processed_at = datetime.now(timezone.utc)
            db.commit()

            return {
                "result": "completed",
                "messages_checked": 0,
                "suggestions_created": 0,
            }

        history_result = get_gmail_history_previews(
            gmail_connection=gmail_connection,
            start_history_id=start_history_id,
        )

        sync_result = process_gmail_messages(
            db=db,
            user_id=gmail_connection.user_id,
            gmail_messages=history_result["messages"],
        )

        # These changes commit together. A failure cannot advance Gmail
        # history without also saving its suggestions and job result.
        gmail_connection.gmail_history_id = (
            history_result["latest_history_id"]
        )
        job.status = "completed"
        job.processed_at = datetime.now(timezone.utc)

        db.commit()

        return {
            "result": "completed",
            "messages_checked": sync_result["messages_checked"],
            "duplicates_skipped": sync_result["duplicates_skipped"],
            "irrelevant_messages": sync_result[
                "irrelevant_messages"
            ],
            "suggestions_created": sync_result[
                "suggestions_created"
            ],
        }
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def run_worker() -> None:
    """
    Polls PostgreSQL, processes jobs, and renews Gmail watches.
    """

    recovered_count = recover_interrupted_jobs()

    logger.info(
        "Gmail worker started. Recovered %s interrupted job(s).",
        recovered_count,
    )

    next_renewal_check = 0.0

    while True:
        job_id = None

        if time.monotonic() >= next_renewal_check:
            try:
                renewal_result = renew_gmail_watches()

                logger.info(
                    "Gmail watch renewal check finished: %s",
                    renewal_result,
                )
            except Exception:
                logger.exception(
                    "The Gmail watch renewal check failed."
                )
            finally:
                next_renewal_check = (
                    time.monotonic()
                    + WATCH_RENEWAL_CHECK_SECONDS
                )

        try:
            job_id = claim_next_job()

            if job_id is None:
                time.sleep(POLL_INTERVAL_SECONDS)
                continue

            result = process_job(job_id)

            logger.info(
                "Job %s finished: %s",
                job_id,
                result,
            )
        except KeyboardInterrupt:
            logger.info("Gmail worker stopped.")
            return
        except Exception as error:
            if job_id is not None:
                mark_job_failed(
                    job_id=job_id,
                    error=error,
                )
            else:
                logger.exception(
                    "The worker could not claim a job."
                )

            time.sleep(POLL_INTERVAL_SECONDS)


if __name__ == "__main__":
    if "--renew-watches-now" in sys.argv[1:]:
        renewal_result = renew_gmail_watches(force=True)
        logger.info(
            "Forced Gmail watch renewal finished: %s",
            renewal_result,
        )
    else:
        run_worker()