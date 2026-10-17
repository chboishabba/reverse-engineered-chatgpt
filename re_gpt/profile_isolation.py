"""Profile-path isolation for concurrent browser bootstrap/fallback launches."""

from __future__ import annotations

import json
import os
from pathlib import Path
import time
from typing import Any


def canonical_profile_dir(profile_dir: Path | str) -> Path:
    return Path(profile_dir).expanduser().resolve(strict=False)


class ProfileDirectoryLease:
    """Atomic path-local lease: same profile serializes, different profiles do not."""

    def __init__(
        self,
        profile_dir: Path | str,
        *,
        timeout_seconds: float = 60.0,
        poll_interval: float = 0.1,
        unparseable_grace_seconds: float = 5.0,
    ) -> None:
        self.profile_dir = canonical_profile_dir(profile_dir)
        self.timeout_seconds = max(0.0, float(timeout_seconds))
        self.poll_interval = max(0.01, float(poll_interval))
        self.unparseable_grace_seconds = max(0.0, float(unparseable_grace_seconds))
        self.lease_path = self.profile_dir / ".re_gpt_profile_lease"
        self._owned = False

    @staticmethod
    def _pid_alive(pid: int) -> bool:
        try:
            os.kill(pid, 0)
            return True
        except ProcessLookupError:
            return False
        except PermissionError:
            return True

    def _read_owner(self) -> dict[str, Any]:
        try:
            raw = json.loads(self.lease_path.read_text(encoding="utf-8"))
            return raw if isinstance(raw, dict) else {}
        except Exception:
            return {}

    def _remove_if_stale(self) -> bool:
        owner = self._read_owner()
        try:
            pid = int(owner.get("pid"))
        except (TypeError, ValueError):
            pid = 0
        if pid > 0:
            if self._pid_alive(pid):
                return False
        else:
            try:
                age = max(0.0, time.time() - self.lease_path.stat().st_mtime)
            except OSError:
                age = self.unparseable_grace_seconds + 1.0
            if age <= self.unparseable_grace_seconds:
                return False
        try:
            self.lease_path.unlink()
            return True
        except FileNotFoundError:
            return True
        except OSError:
            return False

    def acquire(self) -> "ProfileDirectoryLease":
        self.profile_dir.mkdir(parents=True, exist_ok=True)
        deadline = time.monotonic() + self.timeout_seconds
        while True:
            try:
                fd = os.open(self.lease_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            except FileExistsError:
                if self._remove_if_stale():
                    continue
                if time.monotonic() >= deadline:
                    raise RuntimeError(
                        f"browser profile is already in use: {self.profile_dir}"
                    )
                time.sleep(self.poll_interval)
                continue
            try:
                payload = json.dumps({"pid": os.getpid(), "created_at": time.time()})
                os.write(fd, payload.encode("utf-8"))
                os.fsync(fd)
            finally:
                os.close(fd)
            self._owned = True
            return self

    def release(self) -> None:
        if not self._owned:
            return
        try:
            self.lease_path.unlink()
        except FileNotFoundError:
            pass
        finally:
            self._owned = False

    def __enter__(self) -> "ProfileDirectoryLease":
        return self.acquire()

    def __exit__(self, exc_type, exc, tb) -> None:
        self.release()
