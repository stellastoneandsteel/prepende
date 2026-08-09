"""Conversation route handlers."""

from __future__ import annotations

from typing import Any, Mapping

from services.auth_context import AuthError, require_auth
from services.conversation_service import ConversationError, ConversationService


def handle_get(path: str, headers: Mapping[str, Any]) -> tuple[int, dict[str, Any]]:
    try:
        auth = require_auth(headers)
        service = ConversationService()
        if path == "/conversations":
            return 200, {"conversations": service.list_conversations(auth)}

        prefix = "/conversations/"
        if path.startswith(prefix) and path.endswith("/messages"):
            conversation_id = path[len(prefix):-len("/messages")].strip("/")
            return 200, {"messages": service.list_messages(auth, conversation_id)}

        if path.startswith(prefix):
            conversation_id = path[len(prefix):].strip("/")
            return 200, {"conversation": service.get_conversation(auth, conversation_id)}

        return 404, {"error": "not found"}
    except AuthError as exc:
        return exc.status, {"error": str(exc)}
    except ConversationError as exc:
        return exc.status, {"error": str(exc)}


def handle_post(path: str, data: dict[str, Any], headers: Mapping[str, Any]) -> tuple[int, dict[str, Any]]:
    try:
        auth = require_auth(headers)
        service = ConversationService()
        if path == "/conversations":
            conversation = service.create_conversation(auth, data)
            return 201, {"conversation": conversation}

        prefix = "/conversations/"
        suffix = "/messages"
        if path.startswith(prefix) and path.endswith(suffix):
            conversation_id = path[len(prefix):-len(suffix)].strip("/")
            result = service.add_user_message(auth, conversation_id, data)
            return 200, result

        return 404, {"error": "not found"}
    except AuthError as exc:
        return exc.status, {"error": str(exc)}
    except ConversationError as exc:
        return exc.status, {"error": str(exc)}


def handle_patch(path: str, data: dict[str, Any], headers: Mapping[str, Any]) -> tuple[int, dict[str, Any]]:
    try:
        auth = require_auth(headers)
        service = ConversationService()
        prefix = "/conversations/"
        if path.startswith(prefix):
            conversation_id = path[len(prefix):].strip("/")
            return 200, {"conversation": service.update_conversation(auth, conversation_id, data)}
        return 404, {"error": "not found"}
    except AuthError as exc:
        return exc.status, {"error": str(exc)}
    except ConversationError as exc:
        return exc.status, {"error": str(exc)}


def handle_delete(path: str, headers: Mapping[str, Any]) -> tuple[int, dict[str, Any]]:
    try:
        auth = require_auth(headers)
        service = ConversationService()
        prefix = "/conversations/"
        if path.startswith(prefix):
            conversation_id = path[len(prefix):].strip("/")
            return 200, service.delete_conversation(auth, conversation_id)
        return 404, {"error": "not found"}
    except AuthError as exc:
        return exc.status, {"error": str(exc)}
    except ConversationError as exc:
        return exc.status, {"error": str(exc)}
