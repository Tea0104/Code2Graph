from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import subprocess
import time


def _as_text(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


@dataclass(frozen=True)
class CommandResult:
    command: list[str]
    cwd: str
    returncode: int
    stdout: str
    stderr: str
    elapsed_seconds: float
    timed_out: bool = False

    @property
    def ok(self) -> bool:
        return self.returncode == 0 and not self.timed_out

    def excerpt(self, limit: int = 4000) -> str:
        text = "\n".join(
            item for item in [self.stdout.strip(), self.stderr.strip()] if item
        )
        return text[-limit:]


def run_command(
    command: list[str],
    *,
    cwd: Path,
    timeout: int,
    env: dict[str, str] | None = None,
) -> CommandResult:
    started = time.perf_counter()
    try:
        completed = subprocess.run(
            command,
            cwd=str(cwd),
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
            check=False,
        )
        return CommandResult(
            command=command,
            cwd=str(cwd),
            returncode=completed.returncode,
            stdout=_as_text(completed.stdout),
            stderr=_as_text(completed.stderr),
            elapsed_seconds=round(time.perf_counter() - started, 3),
        )
    except OSError as exc:
        return CommandResult(
            command=command,
            cwd=str(cwd),
            returncode=126,
            stdout="",
            stderr=str(exc),
            elapsed_seconds=round(time.perf_counter() - started, 3),
        )
    except subprocess.TimeoutExpired as exc:
        return CommandResult(
            command=command,
            cwd=str(cwd),
            returncode=124,
            stdout=_as_text(exc.stdout),
            stderr=_as_text(exc.stderr),
            elapsed_seconds=round(time.perf_counter() - started, 3),
            timed_out=True,
        )
