"""Create or update the Kafka topics used by the SEC ingestion pipeline.

The migration is idempotent: missing topics are created and existing topics
get additional partitions when the configured count is higher. Topic
configuration is applied only at creation so later operator tuning is not
overwritten. Consumer groups do not need to be created; the workers register
them on their first poll.

Run it against the host-mapped broker (default ``localhost:9092``)::

    backend/.venv/Scripts/python.exe backend/scripts/kafka_migrate.py

Run it inside the compose network before starting the workers::

    docker compose run --rm backend python scripts/kafka_migrate.py

Environment variables (``backend/.env`` is loaded automatically):

    KAFKA_BOOTSTRAP_SERVERS         default localhost:9092
    KAFKA_TOPIC                     default sec-filings
    KAFKA_STEP2_TOPIC               default sec-filings-index
    KAFKA_GROUP_ID                  default loader
    KAFKA_STEP2_GROUP_ID            default step2
    KAFKA_TOPIC_PARTITIONS          default 3
    KAFKA_STEP2_TOPIC_PARTITIONS    default 3
    KAFKA_TOPIC_REPLICATION_FACTOR  default 1
    KAFKA_TOPIC_RETENTION_MS        default 604800000 (7 days)
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from dataclasses import dataclass
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from aiokafka.admin import AIOKafkaAdminClient, NewPartitions, NewTopic
from aiokafka.errors import KafkaError
from dotenv import load_dotenv

from job_queue import bootstrap_servers, step2_topic_name, topic_name

DEFAULT_RETENTION_MS = 604_800_000
MS_PER_DAY = 86_400_000


def _env_int(name: str, default: int, *, minimum: int = 1) -> int:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise SystemExit(f"{name} must be an integer, got {raw!r}") from exc
    if value < minimum:
        raise SystemExit(f"{name} must be >= {minimum}, got {value}")
    return value


@dataclass(frozen=True)
class TopicSpec:
    name: str
    partitions: int
    replication_factor: int
    retention_ms: int

    @property
    def configs(self) -> dict[str, str]:
        return {
            "cleanup.policy": "delete",
            "retention.ms": str(self.retention_ms),
            "min.insync.replicas": str(min(2, self.replication_factor)),
        }


def topic_specs() -> list[TopicSpec]:
    replication_factor = _env_int("KAFKA_TOPIC_REPLICATION_FACTOR", 1)
    retention_ms = _env_int("KAFKA_TOPIC_RETENTION_MS", DEFAULT_RETENTION_MS)
    specs = [
        TopicSpec(
            name=topic_name(),
            partitions=_env_int("KAFKA_TOPIC_PARTITIONS", 3),
            replication_factor=replication_factor,
            retention_ms=retention_ms,
        ),
        TopicSpec(
            name=step2_topic_name(),
            partitions=_env_int("KAFKA_STEP2_TOPIC_PARTITIONS", 3),
            replication_factor=replication_factor,
            retention_ms=retention_ms,
        ),
    ]
    if specs[0].name == specs[1].name:
        raise SystemExit("KAFKA_TOPIC and KAFKA_STEP2_TOPIC must be different")
    return specs


async def _describe_topic(
    admin: AIOKafkaAdminClient, name: str
) -> tuple[int, int]:
    metadata = await admin.describe_topics([name])
    partitions = metadata[0]["partitions"]
    replicas = len(partitions[0]["replicas"]) if partitions else 0
    return len(partitions), replicas


async def _migrate(*, dry_run: bool) -> None:
    servers = bootstrap_servers()
    specs = topic_specs()

    admin = AIOKafkaAdminClient(
        bootstrap_servers=servers,
        client_id="sec-filings-kafka-migrate",
        request_timeout_ms=10_000,
    )
    await admin.start()
    try:
        cluster = await admin.describe_cluster()
        broker_count = len(cluster.get("brokers") or [])
        for spec in specs:
            if spec.replication_factor > broker_count:
                raise SystemExit(
                    f"{spec.name}: replication factor "
                    f"{spec.replication_factor} exceeds the {broker_count} "
                    "available broker(s)"
                )

        existing = set(await admin.list_topics())
        prefix = "would " if dry_run else ""
        mode = " (dry run)" if dry_run else ""
        print(
            f"Kafka topics at {servers} "
            f"(replication={specs[0].replication_factor}){mode}"
        )

        for spec in specs:
            retention_days = spec.retention_ms // MS_PER_DAY
            if spec.name not in existing:
                await admin.create_topics(
                    [
                        NewTopic(
                            name=spec.name,
                            num_partitions=spec.partitions,
                            replication_factor=spec.replication_factor,
                            topic_configs=spec.configs,
                        )
                    ],
                    validate_only=dry_run,
                )
                print(
                    f"  {spec.name}: {prefix}create "
                    f"({spec.partitions} partitions, "
                    f"{retention_days}d retention)"
                )
                continue

            current, current_rf = await _describe_topic(admin, spec.name)
            if current_rf != spec.replication_factor:
                print(
                    f"  {spec.name}: warning: replication factor "
                    f"{current_rf} differs from configured "
                    f"{spec.replication_factor}; it cannot be changed in place"
                )
            if current < spec.partitions:
                await admin.create_partitions(
                    {spec.name: NewPartitions(total_count=spec.partitions)},
                    validate_only=dry_run,
                )
                print(
                    f"  {spec.name}: {prefix}grow "
                    f"{current} -> {spec.partitions} partitions"
                )
            elif current > spec.partitions:
                print(
                    f"  {spec.name}: {current} partitions "
                    f"(configured {spec.partitions}; partitions cannot be shrunk)"
                )
            else:
                print(f"  {spec.name}: ok ({current} partitions)")

        groups = [
            os.getenv("KAFKA_GROUP_ID") or "loader",
            os.getenv("KAFKA_STEP2_GROUP_ID") or "step2",
        ]
        print(
            "Consumer groups are created by the workers on first start: "
            + ", ".join(groups)
        )
    finally:
        await admin.close()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Create the Kafka topics required by the SEC pipeline."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="validate the changes without creating or altering topics",
    )
    args = parser.parse_args()

    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

    load_dotenv(BACKEND_DIR / ".env")

    try:
        asyncio.run(_migrate(dry_run=args.dry_run))
    except (KafkaError, OSError) as exc:
        print(f"Kafka migration failed: {exc}", file=sys.stderr)
        print(
            "Check that Kafka is running and KAFKA_BOOTSTRAP_SERVERS is correct.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
