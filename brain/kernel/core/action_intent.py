"""Classify whether text asks Prepende to perform an outside action.

This is a routing policy, not the execution security boundary.  Callers use it
to decide whether a request belongs in an approval lane; downstream connector
and workflow policies still prevent unapproved effects.  Because under-triggering
cannot bypass those policies, this classifier intentionally prefers request
grammar over bare keyword matching.  Talking about an action, including saying
that none should occur, is not itself an action request.
"""

from __future__ import annotations

import re


# An explicit thinking-only declaration wins before request matching.  This is
# deliberately broad because it can only affect routing; it grants no execution
# authority downstream.
_NO_ACTION_RE = re.compile(
    r"\bread[- ]?only\b|\bno actions?\b|\bno external\b|\bno execution\b"
    r"|\bdo(?:n't| not) (?:execute|act"
    r"|take (?:any |an? )?(?:outside |external )?action"
    r"|perform (?:any |an? )?(?:outside |external )?action)\b"
    r"|\bnothing to (?:execute|run|send|do)\b|\bthinking task\b"
    r"|\b(?:critique|analysis|review|discussion)[- ]only\b"
)

# Verbs that move money, publish, or destroy.  Word boundaries keep discussion
# words such as "sending" and "removed" from becoming requests.
_ACTION_VERB = (
    r"(?:send|email|publish|deploy|delete|remove|erase|wipe"
    r"|charge|purchase|buy|invoice"
    r"|pay(?! (?:no )?attention| heed| mind)"
    r"|post (?:this|that|it|them|to)"
    r")\b"
)

_REQUEST_RE = re.compile(
    r"(?:^|[.!?;:] "
    r"|\bplease "
    r"|\bgo ahead and "
    r"|\bi (?:want|need) you to "
    r"|\bi'd like you to "
    r"|(?<!what )(?<!which )(?<!how )(?<!why )(?<!when )"
    r"\b(?:can|could|will|would) you (?:please )?"
    r")" + _ACTION_VERB
)

# Execution phrases that carry their own verb can be recognized anywhere.  A
# bare noun phrase such as "external action" is intentionally absent: it is
# commonly used while discussing or forbidding effects and has no request verb.
_EXECUTE_ANYWHERE_RE = re.compile(
    r"\b(?:run|execute|start|trigger|fire|kick off)"
    r" (?:an? |the |this |that |my |every )?(?:workflows?|webhooks?|automations?|n8n)\b"
    r"|\b(?:perform|take|execute|start|trigger)"
    r" (?:an? |the )?(?:outside|external) actions?\b"
    r"|\brun this for me\b"
    r"|\b(?:make|process|issue) (?:a|the) payment\b"
)


def looks_like_action_request(message: str) -> bool:
    """Return true only when ``message`` asks Prepende to cause an effect."""

    text = " ".join(str(message).lower().split())
    if not text or _NO_ACTION_RE.search(text):
        return False
    return bool(_REQUEST_RE.search(text) or _EXECUTE_ANYWHERE_RE.search(text))


__all__ = ["looks_like_action_request"]
