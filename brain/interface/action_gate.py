"""Compatibility import for the kernel-owned action-intent policy.

Inbound surfaces historically imported this module.  Keep that public seam
while the Thought Bus and every cockpit share the same kernel classifier.
"""

from kernel.core.action_intent import looks_like_action_request

__all__ = ["looks_like_action_request"]
