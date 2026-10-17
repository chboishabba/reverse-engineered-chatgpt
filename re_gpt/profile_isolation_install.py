"""Install canonical per-profile browser launch leases."""

from __future__ import annotations

import functools
from typing import Any

from .profile_isolation import ProfileDirectoryLease, canonical_profile_dir

_INSTALLED = False


def install_profile_isolation() -> None:
    global _INSTALLED
    if _INSTALLED:
        return

    from .accessibility_browser import AccessibilityBrowserRelay

    original_enter = AccessibilityBrowserRelay.__aenter__
    original_exit = AccessibilityBrowserRelay.__aexit__

    @functools.wraps(original_enter)
    async def isolated_enter(self, *args: Any, **kwargs: Any):
        if getattr(self, "cdp_endpoint", None):
            return await original_enter(self, *args, **kwargs)
        self.profile_dir = canonical_profile_dir(self.profile_dir)
        lease = ProfileDirectoryLease(
            self.profile_dir,
            timeout_seconds=max(0.0, float(getattr(self, "timeout_ms", 60000)) / 1000.0),
        )
        lease.acquire()
        self._re_gpt_profile_lease = lease
        try:
            return await original_enter(self, *args, **kwargs)
        except Exception:
            lease.release()
            self._re_gpt_profile_lease = None
            raise

    @functools.wraps(original_exit)
    async def isolated_exit(self, exc_type, exc, tb):
        try:
            return await original_exit(self, exc_type, exc, tb)
        finally:
            lease = getattr(self, "_re_gpt_profile_lease", None)
            if lease is not None:
                lease.release()
                self._re_gpt_profile_lease = None

    isolated_enter._re_gpt_profile_isolated = True
    isolated_exit._re_gpt_profile_isolated = True
    AccessibilityBrowserRelay.__aenter__ = isolated_enter
    AccessibilityBrowserRelay.__aexit__ = isolated_exit
    _INSTALLED = True
