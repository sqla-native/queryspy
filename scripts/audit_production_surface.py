"""Audit the *published* surface, not the development tree.

`pip-audit` run in the dev virtualenv reports advisories in pytest, mypy, ruff
and friends - none of which a consumer installs. This builds the wheel, installs
only it into a throwaway virtualenv, and audits that closure: queryspy plus
SQLAlchemy, which is exactly what `pip install queryspy` pulls in.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


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

        # Audit the *dependencies*, not the distribution itself. The version
        # being released is by definition not on PyPI yet, and --strict (which
        # we want, so an unresolvable real dependency fails loudly) would treat
        # that as an error on every single release.
        frozen = subprocess.run(
            [str(python), "-m", "pip", "freeze", "--all"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout
        deps = [
            line
            for line in frozen.splitlines()
            if line.strip() and not line.lower().startswith(("queryspy", "pip==", "setuptools=="))
        ]
        print("production closure:", ", ".join(deps) or "(none)")

        requirements = Path(tmp) / "requirements.txt"
        requirements.write_text("\n".join(deps) + "\n")

        run(str(python), "-m", "pip", "install", "--quiet", "pip-audit")
        run(
            str(python),
            "-m",
            "pip_audit",
            "--strict",
            "--progress-spinner",
            "off",
            "-r",
            str(requirements),
        )

    print("\nProduction closure is clean.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
