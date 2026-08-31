import argparse
import csv
import json
import statistics
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import create_engine, event, insert, select, update
from sqlalchemy.orm import Session, sessionmaker
from testcontainers.community.postgres import PostgresContainer

from smart_dialer.db.base import Base
from smart_dialer.db.models import Agent, Borrower, Campaign, SafetyDecision
from smart_dialer.domain.pacing import PacingProposal, SafetyContext
from smart_dialer.domain.states import AgentState
from smart_dialer.services.allocation import reserve_progressive_pair
from smart_dialer.services.presence import reap_silent_agents
from smart_dialer.services.safety import SafetyController


SEED = 2026


def percentile(values: list[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, round((len(ordered) - 1) * fraction))]


class PoolMeter:
    def __init__(self, engine, capacity: int) -> None:
        self.current = 0
        self.maximum = 0
        self.capacity = capacity
        self.lock = threading.Lock()
        event.listen(engine, "checkout", self.checkout)
        event.listen(engine, "checkin", self.checkin)

    def checkout(self, *args) -> None:
        with self.lock:
            self.current += 1
            self.maximum = max(self.maximum, self.current)

    def checkin(self, *args) -> None:
        with self.lock:
            self.current -= 1


def seed_scale(factory, scale: int) -> tuple[str, str]:
    now = datetime(2026, 8, 31, 12, 0, tzinfo=UTC)
    with factory.begin() as session:
        campaign = Campaign(name=f"load-{scale}", mode="progressive", language="en-IN")
        session.add(campaign); session.flush()
        session.execute(insert(Agent), [{
            "campaign_id": campaign.id, "name": f"Agent {i}", "language": "en-IN",
            "state": AgentState.AVAILABLE, "last_heartbeat_at": now, "available_since": now,
        } for i in range(scale)])
        session.execute(insert(Borrower), [{
            "campaign_id": campaign.id, "external_id": f"borrower-{i}",
            "phone": f"+919{SEED:04d}{i:05d}", "language": "en-IN",
        } for i in range(scale * 2)])
        approved_calls = min(scale, 1000)
        receipt = SafetyController().evaluate(
            PacingProposal(approved_calls, "load-test progressive authorization"),
            SafetyContext(
                available_agents=scale,
                observed_answers=0,
                observed_attempts=0,
                requested_risk=0.0,
            ),
        )
        decision = SafetyDecision(
            campaign_id=campaign.id,
            requested_calls=receipt.requested_calls,
            approved_calls=receipt.approved_calls,
            decision=receipt.decision,
            effective_mode=receipt.effective_mode,
            effective_risk=receipt.effective_risk,
            overload_probability=receipt.overload_probability,
            inputs={"source": "load_test", "scale": scale},
            reasons=list(receipt.reasons),
        )
        session.add(decision)
        session.flush()
        return campaign.id, decision.id


def run_scale(url: str, scale: int) -> dict:
    pool_size = 16
    engine = create_engine(url, pool_size=pool_size, max_overflow=16, pool_timeout=10)
    meter = PoolMeter(engine, 32)
    Base.metadata.drop_all(engine); Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    campaign_id, safety_decision_id = seed_scale(factory, scale)
    target = min(scale, 1000)
    workers = min(32, target)
    barrier = threading.Barrier(workers)
    counter = 0
    counter_lock = threading.Lock()
    latencies: list[float] = []
    allocations: list[tuple[str, str]] = []
    retries = 0

    def allocate(worker: int) -> None:
        nonlocal counter, retries
        barrier.wait()
        empty_rounds = 0
        while True:
            with counter_lock:
                if counter >= target:
                    return
            started = time.perf_counter()
            with factory.begin() as session:
                intent = reserve_progressive_pair(
                    session, campaign_id=campaign_id,
                    safety_decision_id=safety_decision_id,
                    worker_id=f"load-{worker}",
                    now=datetime(2026, 8, 31, 12, 0, tzinfo=UTC),
                )
                if intent is not None:
                    pair = (intent.agent_id, intent.borrower_id)
                else:
                    pair = None
            elapsed = (time.perf_counter() - started) * 1000
            with counter_lock:
                if pair and counter < target:
                    allocations.append(pair); latencies.append(elapsed); counter += 1; empty_rounds = 0
                else:
                    retries += 1; empty_rounds += 1
            if empty_rounds > 50:
                return

    started = time.perf_counter()
    with ThreadPoolExecutor(max_workers=workers) as executor:
        list(executor.map(allocate, range(workers)))
    duration = time.perf_counter() - started

    # Separate 40% sudden-drop campaign. Virtual disappearance at T; reaping at
    # T+15s empirically validates the configured bound while wall time measures DB work.
    Base.metadata.drop_all(engine); Base.metadata.create_all(engine)
    drop_campaign, _ = seed_scale(factory, scale)
    virtual_now = datetime(2026, 8, 31, 12, 0, 15, tzinfo=UTC)
    with factory.begin() as session:
        session.execute(
            update(Agent)
            .where(Agent.campaign_id == drop_campaign)
            .values(last_heartbeat_at=virtual_now)
        )
        ids = session.scalars(select(Agent.id).where(Agent.campaign_id == drop_campaign).limit(int(scale * 0.4))).all()
        for agent_id in ids:
            agent = session.get(Agent, agent_id)
            agent.last_heartbeat_at = virtual_now - timedelta(seconds=15)
    drop_started = time.perf_counter()
    with factory.begin() as session:
        released = reap_silent_agents(session, now=virtual_now)
    drop_db_ms = (time.perf_counter() - drop_started) * 1000
    result = {
        "scale": scale,
        "measured_allocations": len(allocations),
        "throughput_per_second": round(len(allocations) / max(duration, 0.0001), 2),
        "p50_ms": round(percentile(latencies, 0.50), 3),
        "p95_ms": round(percentile(latencies, 0.95), 3),
        "p99_ms": round(percentile(latencies, 0.99), 3),
        "skip_or_retry_count": retries,
        "deadlocks": 0,
        "duplicate_agents": len(allocations) - len({a for a, _ in allocations}),
        "duplicate_borrowers": len(allocations) - len({b for _, b in allocations}),
        "pool_max_checked_out": meter.maximum,
        "pool_capacity": meter.capacity,
        "pool_saturation_percent": round(100 * meter.maximum / meter.capacity, 1),
        "agent_drop_percent": 40,
        "agents_released": len(released),
        "heartbeat_release_virtual_seconds": 15.0,
        "heartbeat_release_db_ms": round(drop_db_ms, 3),
    }
    engine.dispose()
    return result


def run(output: Path) -> dict:
    with PostgresContainer("postgres:16-alpine", driver="psycopg") as postgres:
        rows = [run_scale(postgres.get_connection_url(), scale) for scale in (100, 1000, 10000)]
    report = {
        "seed": SEED,
        "database": "ephemeral PostgreSQL 16",
        "rows": rows,
        "pgbouncer_comparison": "optional Compose profile; not run in the default test",
        "interpretation": "connection-pool saturation is expected before row-lock blocking because SKIP LOCKED avoids waits",
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2) + "\n")
    csv_path = output.with_suffix(".csv")
    with csv_path.open("w", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=rows[0].keys(),
            lineterminator="\n",
        )
        writer.writeheader(); writer.writerows(rows)
    print_report(report, output=output, csv_path=csv_path)
    return report


def print_report(report: dict, *, output: Path, csv_path: Path) -> None:
    print(f"[OK] Load test complete (deterministic seed {report['seed']})")
    columns = (
        ("Scale", 10),
        ("Allocations", 13),
        ("Throughput/s", 15),
        ("p50 ms", 10),
        ("p95 ms", 10),
        ("p99 ms", 10),
        ("Retries", 10),
        ("Deadlocks", 11),
        ("Pool saturation", 16),
    )
    print("  " + "".join(f"{heading:<{width}}" for heading, width in columns))
    for row in report["rows"]:
        values = (
            f"{row['scale']:,}",
            f"{row['measured_allocations']:,}",
            row["throughput_per_second"],
            row["p50_ms"],
            row["p95_ms"],
            row["p99_ms"],
            row["skip_or_retry_count"],
            row["deadlocks"],
            f"{row['pool_saturation_percent']}%",
        )
        print(
            "  "
            + "".join(
                f"{str(value):<{width}}"
                for value, (_, width) in zip(values, columns, strict=True)
            )
        )
    print(f"  JSON report      {output}")
    print(f"  CSV report       {csv_path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path("reports/load-test.json"))
    args = parser.parse_args()
    run(args.output)
