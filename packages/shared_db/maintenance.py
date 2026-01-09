from __future__ import annotations

import argparse
import logging
import time
from datetime import UTC, datetime, timedelta

from packages.shared_db.logging import configure_logging
from packages.shared_db.models import Answer, Query, Source
from packages.shared_db.session import SessionLocal
from packages.shared_db.settings import settings
from packages.shared_db.storage import source_path

logger = logging.getLogger(__name__)


def _cutoff(days: int) -> datetime | None:
    if days <= 0:
        return None
    return datetime.now(tz=UTC) - timedelta(days=days)


def _prune_answers(session, cutoff_at: datetime, dry_run: bool) -> int:
    query = session.query(Answer).filter(Answer.created_at < cutoff_at)
    if dry_run:
        return query.count()
    return query.delete(synchronize_session=False)


def _prune_queries(session, cutoff_at: datetime, dry_run: bool) -> int:
    query = session.query(Query).filter(Query.created_at < cutoff_at)
    if dry_run:
        return query.count()
    return query.delete(synchronize_session=False)


def _prune_sources(
    session, cutoff_at: datetime, batch_size: int, dry_run: bool
) -> int:
    total = 0
    while True:
        sources = (
            session.query(Source)
            .filter(Source.created_at < cutoff_at)
            .order_by(Source.created_at.asc())
            .limit(batch_size)
            .all()
        )
        if not sources:
            break
        total += len(sources)
        if dry_run:
            break
        for source in sources:
            session.delete(source)
        session.commit()
        for source in sources:
            try:
                path = source_path(str(source.id), source.source_type)
                path.unlink(missing_ok=True)
            except OSError:
                logger.warning(
                    "source_file_cleanup_failed",
                    extra={"source_id": str(source.id)},
                )
    return total


def run_prune(
    *,
    sources_days: int,
    answers_days: int,
    queries_days: int,
    batch_size: int,
    dry_run: bool,
    force: bool,
) -> int:
    if not settings.retention_enabled and not force:
        logger.info(
            "retention_disabled",
            extra={"message": "Set RETENTION_ENABLED=true or pass --force."},
        )
        return 0

    sources_cutoff = _cutoff(sources_days)
    answers_cutoff = _cutoff(answers_days)
    queries_cutoff = _cutoff(queries_days)

    if not any((sources_cutoff, answers_cutoff, queries_cutoff)):
        logger.info("retention_noop", extra={"message": "No retention windows configured."})
        return 0

    session = SessionLocal()
    try:
        answers_deleted = 0
        queries_deleted = 0
        sources_deleted = 0
        if answers_cutoff:
            answers_deleted = _prune_answers(session, answers_cutoff, dry_run)
        if queries_cutoff:
            queries_deleted = _prune_queries(session, queries_cutoff, dry_run)
        if sources_cutoff:
            sources_deleted = _prune_sources(
                session, sources_cutoff, batch_size=batch_size, dry_run=dry_run
            )
        if dry_run:
            session.rollback()
        else:
            session.commit()
        logger.info(
            "retention_prune_complete",
            extra={
                "dry_run": dry_run,
                "answers_deleted": answers_deleted,
                "queries_deleted": queries_deleted,
                "sources_deleted": sources_deleted,
            },
        )
    finally:
        session.close()
    return 0


def main() -> None:
    configure_logging("maintenance", settings.log_level)
    parser = argparse.ArgumentParser(description="Prune retained data by age.")
    parser.add_argument(
        "--sources-days",
        type=int,
        default=settings.retention_days_sources,
        help="Delete sources older than N days (0 disables).",
    )
    parser.add_argument(
        "--answers-days",
        type=int,
        default=settings.retention_days_answers,
        help="Delete answers older than N days (0 disables).",
    )
    parser.add_argument(
        "--queries-days",
        type=int,
        default=settings.retention_days_queries,
        help="Delete queries older than N days (0 disables).",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=settings.retention_batch_size,
        help="Batch size for source deletes.",
    )
    parser.add_argument("--dry-run", action="store_true", help="Log counts without deleting.")
    parser.add_argument(
        "--force",
        action="store_true",
        help="Run even when RETENTION_ENABLED is false.",
    )
    parser.add_argument(
        "--interval",
        type=int,
        default=0,
        help="Loop every N seconds (0 runs once).",
    )
    args = parser.parse_args()

    interval = max(0, args.interval)
    while True:
        run_prune(
            sources_days=args.sources_days,
            answers_days=args.answers_days,
            queries_days=args.queries_days,
            batch_size=max(1, args.batch_size),
            dry_run=args.dry_run,
            force=args.force,
        )
        if interval <= 0:
            break
        time.sleep(interval)


if __name__ == "__main__":
    main()
