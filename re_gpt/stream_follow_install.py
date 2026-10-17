"""Install stream-first follow while preserving one-dispatch mutation semantics."""

from __future__ import annotations

import functools
from typing import Any

from .stream_runtime import current_stream_state, stream_runtime

_INSTALLED = False


def install_stream_first_follow() -> None:
    global _INSTALLED
    if _INSTALLED:
        return

    from . import async_mutation_probe, direct_graph_mutation, mutation_service

    original_submit = async_mutation_probe.submit_async_prompt_once

    @functools.wraps(original_submit)
    def stream_submit(*args: Any, progress_callback=None, **kwargs: Any) -> dict[str, Any]:
        active = current_stream_state()
        if active is not None:
            if progress_callback is not None and active.progress_callback is None:
                active.progress_callback = progress_callback
            result = original_submit(*args, **kwargs)
            return dict(result) | active.summary()
        with stream_runtime(progress_callback) as state:
            result = original_submit(*args, **kwargs)
            return dict(result) | state.summary()

    stream_submit._re_gpt_stream_first = True
    async_mutation_probe.submit_async_prompt_once = stream_submit
    mutation_service.submit_async_prompt_once = stream_submit

    original_graph_submit = direct_graph_mutation.submit_direct_graph_mutation_once

    @functools.wraps(original_graph_submit)
    def stream_graph_submit(operation: str, *args: Any, progress_callback=None, **kwargs: Any) -> dict[str, Any]:
        active = current_stream_state()
        if active is not None:
            if progress_callback is not None and active.progress_callback is None:
                active.progress_callback = progress_callback
            result = original_graph_submit(operation, *args, **kwargs)
            return dict(result) | active.summary()
        with stream_runtime(progress_callback) as state:
            result = original_graph_submit(operation, *args, **kwargs)
            return dict(result) | state.summary()

    stream_graph_submit._re_gpt_stream_first = True
    direct_graph_mutation.submit_direct_graph_mutation_once = stream_graph_submit
    mutation_service.submit_direct_graph_mutation_once = stream_graph_submit

    original_continue = mutation_service.MutationService.continue_thread

    @functools.wraps(original_continue)
    def stream_continue(self, *args: Any, progress_callback=None, **kwargs: Any) -> dict[str, Any]:
        follow = bool(kwargs.get("follow", False))
        if not follow:
            return original_continue(self, *args, **kwargs)
        no_poll = dict(kwargs)
        no_poll["follow"] = False
        no_poll["reconcile_attempts"] = 1
        no_poll["reconcile_interval"] = 0.0
        with stream_runtime(progress_callback) as state:
            result = original_continue(self, *args, **no_poll)
            return dict(result) | state.summary() | {
                "follow_mode": "stream_terminal_single_reconcile",
                "reconcile_poll_count": 0,
            }

    stream_continue._re_gpt_stream_first = True
    mutation_service.MutationService.continue_thread = stream_continue

    original_run_graph = mutation_service.MutationService._run_graph_mutation

    @functools.wraps(original_run_graph)
    def stream_graph_mutation(self, operation: str, *args: Any, **kwargs: Any) -> dict[str, Any]:
        follow = bool(kwargs.get("follow", False))
        if not follow:
            return original_run_graph(self, operation, *args, **kwargs)
        no_poll = dict(kwargs)
        no_poll["follow"] = False
        no_poll["reconcile_attempts"] = 1
        no_poll["reconcile_interval"] = 0.0
        with stream_runtime(None) as state:
            result = original_run_graph(self, operation, *args, **no_poll)
            return dict(result) | state.summary() | {
                "follow_mode": "stream_terminal_single_reconcile",
                "reconcile_poll_count": 0,
            }

    stream_graph_mutation._re_gpt_stream_first = True
    mutation_service.MutationService._run_graph_mutation = stream_graph_mutation

    _INSTALLED = True
