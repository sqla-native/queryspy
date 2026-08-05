"""How much does recording actually cost?

Committed rather than run once and forgotten, because the docs make a claim
about where the cost is and that claim should stay checkable.

    python scripts/benchmark.py

History: before 0.3, `str(state.statement)` ran on every recorded query - a full
statement compile - and accounted for roughly half of all recording overhead.
Recording made queries 2.7x slower, and the docs wrongly blamed stack capture.
Rendering is now deferred until a record is actually reported, which cut the
overhead from ~172% to ~36% of baseline and made stack capture genuinely the
largest remaining component.
"""

from __future__ import annotations

import time
from collections.abc import Callable

from sqlalchemy import ForeignKey, String, create_engine, select
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, relationship
from sqlalchemy.pool import StaticPool

from queryspy import record


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "user"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(50))
    addresses: Mapped[list[Address]] = relationship()


class Address(Base):
    __tablename__ = "address"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("user.id"))


engine = create_engine("sqlite://", poolclass=StaticPool)
Base.metadata.create_all(engine)
with Session(engine) as s:
    s.add_all([User(name=f"u{n}", addresses=[Address()]) for n in range(200)])
    s.commit()

N = 400


def workload(session: Session) -> None:
    """A loop of ordinary primary-key fetches - the shape of a real suite."""
    for i in range(1, N + 1):
        session.get(User, (i % 200) + 1)
        session.expunge_all()


ROUNDS = 7


def bench(label: str, fn: Callable[[Session], None]) -> float:
    """Best of ROUNDS.

    A single run is far too noisy to publish a percentage from - an early
    version of this script reported stack-capture overhead as 14% and then 0.9%
    on consecutive runs. The minimum is the standard choice for a microbenchmark:
    it is the run least disturbed by everything else on the machine.
    """
    with Session(engine) as session:
        fn(session)  # warm

    best = float("inf")
    for _ in range(ROUNDS):
        with Session(engine) as session:
            start = time.perf_counter()
            fn(session)
            best = min(best, time.perf_counter() - start)
    print(f"  {label:<42} {best * 1000:8.1f} ms   {best / N * 1e6:7.1f} us/query")
    return best


print(f"\n{N} queries per run, best of {ROUNDS}\n")
base = bench("no recording", workload)


def with_full(session: Session) -> None:
    with record():
        workload(session)


def with_no_stacks(session: Session) -> None:
    with record(capture_stacks=False):
        workload(session)


full = bench("record() with stack capture", with_full)
nostack = bench("record(capture_stacks=False)", with_no_stacks)

print()
print(f"  stack capture overhead      {(full - nostack) / base * 100:6.1f}% of baseline")
print(f"  everything-else overhead    {(nostack - base) / base * 100:6.1f}% of baseline")
print(f"  total recording overhead    {(full - base) / base * 100:6.1f}% of baseline")

# What deferring bought: this is the per-query cost that used to be paid
# unconditionally, and is now paid only for records that get reported.
statement = select(User).where(User.id == 1)
start = time.perf_counter()
for _ in range(N):
    " ".join(str(statement).split())
compile_cost = time.perf_counter() - start
print(
    f"\n  str(statement), now deferred              {compile_cost * 1000:8.1f} ms   "
    f"{compile_cost / N * 1e6:7.1f} us/query"
)
print(f"  (paying it per query would add          {compile_cost / base * 100:6.1f}% of baseline)")
