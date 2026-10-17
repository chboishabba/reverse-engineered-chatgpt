from .async_chatgpt import AsyncChatGPT
from .sync_chatgpt import SyncChatGPT
from .read_plane import install_sync_fetch_chat_compatibility
from .dom_commit import install_dom_commit_receipts
from .mutation_compat import install_unified_sync_mutation_plane
from .session_precommit import install_session_precommit_guard
from .sse_identity import extract_graph_sse_identity
from . import direct_graph_mutation as _direct_graph_mutation

# Library callers that still hold SyncConversation objects get the same safe
# validated read semantics as the supported re-gpt executable.
install_sync_fetch_chat_compatibility()

# Legacy SyncConversation.chat / interactive CLI mutations are routed through
# the same API-first MutationService used by MCP and the supported CLI.
install_unified_sync_mutation_plane()

# Persisted session expiry that is provably stale is rejected before any
# MutationService transport is allowed to dispatch.
install_session_precommit_guard()

# curl_cffi callbacks may split one SSE event across arbitrary chunks. Install
# the fragment-safe identity parser while keeping the direct module's public API.
_direct_graph_mutation.extract_sse_identity = extract_graph_sse_identity

# Browser relay entrypoints get a submission receipt boundary so a no-op click
# is distinct from a committed-but-slow mutation.
install_dom_commit_receipts()
