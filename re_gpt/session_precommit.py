"""Install a pre-dispatch stale-session guard on MutationService entrypoints."""

from __future__ import annotations

from functools import wraps
from typing import Any, Callable

from .session_lifecycle import SessionLifecycle

_INSTALLED = False


def _guarded(method: Callable[..., Any]) -> Callable[..., Any]:
    @wraps(method)
    def wrapper(self, *args: Any, **kwargs: Any) -> Any:
        # The default snapshot path matters too: direct transports load it even
        # when callers omit profile_dir. Missing/opaque-expiry state is allowed;
        # only a provably expired persisted credential blocks dispatch.
        SessionLifecycle().assert_precommit_ready(profile_dir=kwargs.get("profile_dir"))
        return method(self, *args, **kwargs)
    return wrapper


def install_session_precommit_guard() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    from .mutation_service import MutationService

    for name in ("continue_thread", "new_conversation", "branch_from_node", "regenerate", "edit_and_branch"):
        original = getattr(MutationService, name, None)
        if callable(original) and not getattr(original, "_re_gpt_session_guarded", False):
            wrapped = _guarded(original)
            setattr(wrapped, "_re_gpt_session_guarded", True)
            setattr(MutationService, name, wrapped)
    _INSTALLED = True
