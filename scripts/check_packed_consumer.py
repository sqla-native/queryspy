"""Install the built wheel into a clean virtualenv and use it as a consumer would.

Catches packaging mistakes that the test suite structurally cannot: a module
left out of the wheel, a missing `py.typed`, or a pytest entry point that does
not resolve once installed rather than run from the source tree.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

SMOKE = """
import queryspy
from sqlalchemy import ForeignKey, String, create_engine, select
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, relationship
from sqlalchemy.pool import StaticPool


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "user"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(50))
    addresses: Mapped[list["Address"]] = relationship()


class Address(Base):
    __tablename__ = "address"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("user.id"))


engine = create_engine("sqlite://", poolclass=StaticPool)
Base.metadata.create_all(engine)
with Session(engine) as s:
    s.add_all([User(name=f"u{n}", addresses=[Address()]) for n in range(3)])
    s.commit()

with Session(engine) as s:
    with queryspy.record() as spy:
        for user in s.scalars(select(User)).all():
            list(user.addresses)

findings = spy.findings()
assert [f.kind for f in findings] == ["lazy_load"], findings
assert findings[0].label == "User.addresses", findings
assert spy.query_count == 4, spy.query_count

import importlib.metadata as md
eps = md.entry_points(group="pytest11")
assert any(ep.name == "queryspy" for ep in eps), list(eps)

import importlib.util
spec = importlib.util.find_spec("queryspy")
assert spec is not None and spec.submodule_search_locations is not None
root = spec.submodule_search_locations[0]
import os
assert os.path.exists(os.path.join(root, "py.typed")), "py.typed missing from the wheel"

print("packed consumer OK:", queryspy.__version__)
"""


def run(*args: str) -> None:
    print(f"$ {' '.join(args)}", flush=True)
    subprocess.run(args, check=True)


def main() -> int:
    dist = ROOT / "dist"
    if dist.exists():
        shutil.rmtree(dist)
    run(sys.executable, "-m", "build", "--wheel", str(ROOT))

    wheels = sorted(dist.glob("*.whl"))
    if len(wheels) != 1:
        print(f"expected exactly one wheel, found {len(wheels)}", file=sys.stderr)
        return 1

    with tempfile.TemporaryDirectory() as tmp:
        venv = Path(tmp) / "venv"
        run(sys.executable, "-m", "venv", str(venv))
        python = venv / "bin" / "python"
        run(str(python), "-m", "pip", "install", "--quiet", "--upgrade", "pip")
        run(str(python), "-m", "pip", "install", "--quiet", str(wheels[0]))

        smoke = Path(tmp) / "smoke.py"
        smoke.write_text(SMOKE)
        run(str(python), str(smoke))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
