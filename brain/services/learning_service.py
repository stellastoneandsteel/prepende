"""Workspace-scoped learning core for Engram Actions.

The learning core keeps the V2 surface incremental: journal entries, extracted
entities, relationship records, projects, knowledge items, and research findings
all use the same workspace-scoped Firestore boundary and review states.
"""

from __future__ import annotations

import re
import time
import uuid
from typing import Any

from services.firestore_client import FirestoreClient, get_firestore_client


REVIEW_STATES = {"discovered", "summarized", "pending_review", "accepted", "rejected", "archived"}
AGENT_TYPES = {
    "research_agent",
    "watchtower_agent",
    "source_verification_agent",
    "knowledge_distillation_agent",
    "graph_integration_agent",
}


class LearningService:
    def __init__(self, client: FirestoreClient | None = None) -> None:
        self.client = client or get_firestore_client()

    def extract_entities(self, text: str) -> dict[str, Any]:
        clean = _require_text(text, "text", 12000)
        people = _extract_people(clean)
        projects = _extract_projects(clean)
        topics = _extract_topics(clean)
        commitments = _extract_commitments(clean)
        tasks = _extract_tasks(clean)
        goals = _extract_goals(clean)
        durable = _extract_durable_memories(clean, people, projects, topics, commitments)
        entities = [
            *[{"type": "person", "name": name} for name in people],
            *[{"type": "project", "name": name} for name in projects],
            *[{"type": "topic", "name": name} for name in topics],
        ]
        return {
            "entities": entities,
            "people": people,
            "projects": projects,
            "topics": topics,
            "goals": goals,
            "tasks": tasks,
            "commitments": commitments,
            "emotionalTone": _emotional_tone(clean),
            "entryTypes": _journal_types(clean),
            "durableMemories": durable,
        }

    def write_journal_entry(self, workspace_id: str, data: dict[str, Any]) -> dict[str, Any]:
        text = _require_text(str(data.get("text") or data.get("entry") or ""), "text", 12000)
        extracted = self.extract_entities(text)
        now = _now()
        journal_id = _id("jrnl")
        entry = {
            "id": journal_id,
            "workspaceId": workspace_id,
            "text": text,
            "entryDate": str(data.get("entryDate") or now[:10]),
            "source": str(data.get("source") or "gpt_action"),
            "status": "accepted",
            "entryTypes": extracted["entryTypes"],
            "emotionalTone": extracted["emotionalTone"],
            "topics": extracted["topics"],
            "entities": extracted["entities"],
            "projects": extracted["projects"],
            "people": extracted["people"],
            "goals": extracted["goals"],
            "tasks": extracted["tasks"],
            "commitments": extracted["commitments"],
            "durableMemories": extracted["durableMemories"],
            "createdAt": now,
            "updatedAt": now,
        }
        self.client.set_document(_path(workspace_id, "journalEntries", journal_id), entry)
        upserts = {
            "people": [self.upsert_person(workspace_id, {"name": name, "relationshipType": "mentioned"}) for name in extracted["people"]],
            "projects": [self.upsert_project(workspace_id, {"name": name, "status": "active"}) for name in extracted["projects"]],
        }
        return {"journalEntry": _public(entry), "extracted": extracted, "upserts": upserts}

    def upsert_person(self, workspace_id: str, data: dict[str, Any]) -> dict[str, Any]:
        name = _require_text(str(data.get("name") or ""), "name", 200)
        existing = self._find_by_name(workspace_id, "people", name)
        person_id = str(data.get("personId") or (existing or {}).get("id") or _id("person"))
        now = _now()
        previous = existing or {}
        key_facts = _merge_list(previous.get("keyFacts"), data.get("keyFacts"))
        commitments = _merge_list(previous.get("openCommitments"), data.get("openCommitments"))
        project_ids = _merge_list(previous.get("projectAssociations"), data.get("projectAssociations"))
        summaries = _merge_list(previous.get("communicationSummaries"), data.get("communicationSummaries"))
        item = {
            "id": person_id,
            "workspaceId": workspace_id,
            "name": name,
            "role": str(data.get("role") or previous.get("role") or ""),
            "organization": str(data.get("organization") or previous.get("organization") or ""),
            "relationshipType": str(data.get("relationshipType") or previous.get("relationshipType") or "contact"),
            "lastInteraction": str(data.get("lastInteraction") or previous.get("lastInteraction") or now),
            "keyFacts": key_facts,
            "openCommitments": commitments,
            "projectAssociations": project_ids,
            "communicationSummaries": summaries,
            "followUpSuggestions": _merge_list(previous.get("followUpSuggestions"), data.get("followUpSuggestions")),
            "sentimentContext": str(data.get("sentimentContext") or previous.get("sentimentContext") or ""),
            "status": str(data.get("status") or previous.get("status") or "active"),
            "createdAt": previous.get("createdAt") or now,
            "updatedAt": now,
        }
        self.client.set_document(_path(workspace_id, "people", person_id), item)
        return _public(item)

    def update_relationship_graph(self, workspace_id: str, data: dict[str, Any]) -> dict[str, Any]:
        source_id = _require_text(str(data.get("sourcePersonId") or data.get("personId") or ""), "sourcePersonId", 120)
        target_id = _require_text(str(data.get("targetId") or data.get("projectId") or data.get("memoryId") or ""), "targetId", 160)
        edge_type = str(data.get("relationshipType") or data.get("edgeType") or "related_to").strip() or "related_to"
        now = _now()
        edge_id = str(data.get("edgeId") or _id("rel"))
        edge = {
            "id": edge_id,
            "workspaceId": workspace_id,
            "sourcePersonId": source_id,
            "targetId": target_id,
            "targetType": str(data.get("targetType") or "project"),
            "relationshipType": edge_type,
            "summary": str(data.get("summary") or ""),
            "sentimentContext": str(data.get("sentimentContext") or ""),
            "status": str(data.get("status") or "active"),
            "createdAt": now,
            "updatedAt": now,
        }
        self.client.set_document(_path(workspace_id, "relationshipEdges", edge_id), edge)
        return _public(edge)

    def get_relationship_context(self, workspace_id: str, data: dict[str, Any]) -> dict[str, Any]:
        name = str(data.get("name") or "").strip()
        person_id = str(data.get("personId") or "").strip()
        person = self._find_by_name(workspace_id, "people", name) if name and not person_id else None
        if person_id:
            person = self.client.get_document(_path(workspace_id, "people", person_id))
        if not person or person.get("workspaceId") != workspace_id:
            return {"person": None, "edges": [], "projects": [], "warnings": ["No relationship found."]}
        edges = [
            item for item in self.client.list_documents(_collection(workspace_id, "relationshipEdges"), limit=100)
            if item.get("workspaceId") == workspace_id and item.get("sourcePersonId") == person["id"] and item.get("status") != "deleted"
        ]
        projects = [
            self.client.get_document(_path(workspace_id, "projects", str(edge.get("targetId"))))
            for edge in edges
            if edge.get("targetType") == "project"
        ]
        return {
            "person": _public(person),
            "edges": [_public(edge) for edge in edges],
            "projects": [_public(project) for project in projects if project],
            "warnings": [],
        }

    def upsert_project(self, workspace_id: str, data: dict[str, Any]) -> dict[str, Any]:
        name = _require_text(str(data.get("name") or data.get("projectName") or ""), "name", 200)
        existing = self._find_by_name(workspace_id, "projects", name)
        project_id = str(data.get("projectId") or (existing or {}).get("id") or _id("proj"))
        now = _now()
        previous = existing or {}
        item = {
            "id": project_id,
            "workspaceId": workspace_id,
            "name": name,
            "purpose": str(data.get("purpose") or previous.get("purpose") or ""),
            "currentStatus": str(data.get("currentStatus") or data.get("status") or previous.get("currentStatus") or "active"),
            "relatedPeople": _merge_list(previous.get("relatedPeople"), data.get("relatedPeople")),
            "relatedTasks": _merge_list(previous.get("relatedTasks"), data.get("relatedTasks")),
            "relatedKnowledge": _merge_list(previous.get("relatedKnowledge"), data.get("relatedKnowledge")),
            "relatedMemories": _merge_list(previous.get("relatedMemories"), data.get("relatedMemories")),
            "majorDecisions": _merge_list(previous.get("majorDecisions"), data.get("majorDecisions")),
            "activeRisks": _merge_list(previous.get("activeRisks"), data.get("activeRisks")),
            "nextActions": _merge_list(previous.get("nextActions"), data.get("nextActions")),
            "createdAt": previous.get("createdAt") or now,
            "updatedAt": now,
        }
        self.client.set_document(_path(workspace_id, "projects", project_id), item)
        return _public(item)

    def link_memory_to_project(self, workspace_id: str, data: dict[str, Any]) -> dict[str, Any]:
        project_id = _require_text(str(data.get("projectId") or ""), "projectId", 120)
        memory_id = _require_text(str(data.get("memoryId") or ""), "memoryId", 160)
        project = self.client.get_document(_path(workspace_id, "projects", project_id))
        if not project or project.get("workspaceId") != workspace_id:
            raise LookupError("project not found")
        project["relatedMemories"] = _merge_list(project.get("relatedMemories"), [memory_id])
        project["updatedAt"] = _now()
        self.client.set_document(_path(workspace_id, "projects", project_id), project)
        link = {
            "id": _id("plink"),
            "workspaceId": workspace_id,
            "projectId": project_id,
            "memoryId": memory_id,
            "relationshipType": str(data.get("relationshipType") or "supports_project_context"),
            "createdAt": _now(),
        }
        self.client.set_document(_path(workspace_id, "projectMemoryLinks", link["id"]), link)
        return {"project": _public(project), "link": _public(link)}

    def get_project_context(self, workspace_id: str, data: dict[str, Any]) -> dict[str, Any]:
        project_id = str(data.get("projectId") or "").strip()
        name = str(data.get("name") or data.get("projectName") or "").strip()
        project = self.client.get_document(_path(workspace_id, "projects", project_id)) if project_id else None
        if not project and name:
            project = self._find_by_name(workspace_id, "projects", name)
        if not project or project.get("workspaceId") != workspace_id:
            return {"project": None, "warnings": ["No project found."]}
        knowledge = [
            item for item in self.client.list_documents(_collection(workspace_id, "knowledgeItems"), limit=100)
            if project["id"] in _as_list(item.get("relatedProjects")) and item.get("status") == "accepted"
        ]
        return {"project": _public(project), "knowledge": [_public(item) for item in knowledge], "warnings": []}

    def ingest_knowledge(self, workspace_id: str, data: dict[str, Any]) -> dict[str, Any]:
        content = _require_text(str(data.get("content") or data.get("claim") or ""), "content", 12000)
        provenance = data.get("provenance") or data.get("sourceReferences") or data.get("source")
        source_refs = _normalize_source_refs(provenance)
        if not source_refs:
            raise ValueError("provenance or sourceReferences are required")
        from_agent = bool(data.get("agentId") or data.get("findingId") or data.get("requiresApproval"))
        status = str(data.get("status") or "accepted")
        if from_agent and not data.get("approved"):
            status = "pending_review"
        if status not in REVIEW_STATES:
            status = "pending_review"
        now = _now()
        item_id = str(data.get("knowledgeId") or _id("know"))
        item = {
            "id": item_id,
            "workspaceId": workspace_id,
            "content": content,
            "concepts": _as_list(data.get("concepts")) or _extract_topics(content),
            "claims": _as_list(data.get("claims")) or [content],
            "definitions": _as_list(data.get("definitions")),
            "sourceReferences": source_refs,
            "confidence": _clamp_float(data.get("confidence"), 0.7),
            "contradictions": _as_list(data.get("contradictions")),
            "relatedProjects": _as_list(data.get("relatedProjects")),
            "relatedPeople": _as_list(data.get("relatedPeople")),
            "relatedMemories": _as_list(data.get("relatedMemories")),
            "retrievalDate": str(data.get("retrievalDate") or now[:10]),
            "status": status,
            "createdAt": now,
            "updatedAt": now,
        }
        self.client.set_document(_path(workspace_id, "knowledgeItems", item_id), item)
        approval = None
        if status == "pending_review":
            approval = self.create_learning_approval(workspace_id, {
                "targetType": "knowledge",
                "targetId": item_id,
                "reason": "Knowledge update requires review before it becomes durable.",
            })
        return {"knowledgeItem": _public(item), "approval": approval}

    def search_knowledge(self, workspace_id: str, data: dict[str, Any]) -> dict[str, Any]:
        query = _require_text(str(data.get("query") or ""), "query", 500)
        limit = _bounded_int(data.get("k") or data.get("limit") or 10, 1, 25)
        words = set(_words(query))
        matches = []
        for item in self.client.list_documents(_collection(workspace_id, "knowledgeItems"), limit=200):
            if item.get("workspaceId") != workspace_id or item.get("status") not in {"accepted", "pending_review"}:
                continue
            haystack = " ".join(str(item.get(key) or "") for key in ("content", "concepts", "claims")).lower()
            score = sum(1 for word in words if word in haystack)
            if score:
                public = _public(item)
                public["score"] = score
                matches.append(public)
        matches.sort(key=lambda item: (-int(item.get("score") or 0), str(item.get("updatedAt") or "")))
        return {"knowledge": matches[:limit], "count": min(len(matches), limit)}

    def update_knowledge_graph(self, workspace_id: str, data: dict[str, Any]) -> dict[str, Any]:
        knowledge_id = _require_text(str(data.get("knowledgeId") or ""), "knowledgeId", 120)
        item = self.client.get_document(_path(workspace_id, "knowledgeItems", knowledge_id))
        if not item or item.get("workspaceId") != workspace_id:
            raise LookupError("knowledge not found")
        for key in ("relatedProjects", "relatedPeople", "relatedMemories", "contradictions", "concepts"):
            if key in data:
                item[key] = _merge_list(item.get(key), data.get(key))
        if "confidence" in data:
            item["confidence"] = _clamp_float(data.get("confidence"), item.get("confidence", 0.7))
        item["updatedAt"] = _now()
        self.client.set_document(_path(workspace_id, "knowledgeItems", knowledge_id), item)
        return _public(item)

    def create_research_agent(self, workspace_id: str, data: dict[str, Any]) -> dict[str, Any]:
        topic = _require_text(str(data.get("topic") or data.get("name") or ""), "topic", 300)
        agent_type = str(data.get("agentType") or "research_agent").strip().lower()
        if agent_type not in AGENT_TYPES:
            agent_type = "research_agent"
        now = _now()
        agent_id = str(data.get("agentId") or _id("agent"))
        item = {
            "id": agent_id,
            "workspaceId": workspace_id,
            "agentType": agent_type,
            "topic": topic,
            "description": str(data.get("description") or ""),
            "status": "active",
            "allowedSources": _as_list(data.get("allowedSources")),
            "reviewPolicy": "findings require user approval before durable knowledge updates",
            "createdAt": now,
            "updatedAt": now,
        }
        self.client.set_document(_path(workspace_id, "researchAgents", agent_id), item)
        return _public(item)

    def run_research_agent(self, workspace_id: str, data: dict[str, Any]) -> dict[str, Any]:
        agent_id = _require_text(str(data.get("agentId") or ""), "agentId", 120)
        agent = self.client.get_document(_path(workspace_id, "researchAgents", agent_id))
        if not agent or agent.get("workspaceId") != workspace_id:
            raise LookupError("agent not found")
        summary = str(data.get("summary") or data.get("finding") or "").strip()
        if not summary:
            summary = f"Research finding proposed for {agent.get('topic')}."
        source_refs = _normalize_source_refs(data.get("sourceReferences") or data.get("provenance") or [])
        if not source_refs:
            source_refs = [{"type": "operator_note", "title": str(agent.get("topic")), "retrievedAt": _now()}]
        now = _now()
        finding_id = _id("finding")
        finding = {
            "id": finding_id,
            "workspaceId": workspace_id,
            "agentId": agent_id,
            "agentType": agent.get("agentType"),
            "topic": agent.get("topic"),
            "summary": summary,
            "sourceReferences": source_refs,
            "status": "pending_review",
            "proposedKnowledge": {
                "content": summary,
                "sourceReferences": source_refs,
                "concepts": _extract_topics(summary),
                "confidence": _clamp_float(data.get("confidence"), 0.6),
            },
            "createdAt": now,
            "updatedAt": now,
        }
        self.client.set_document(_path(workspace_id, "agentFindings", finding_id), finding)
        approval = self.create_learning_approval(workspace_id, {
            "targetType": "agent_finding",
            "targetId": finding_id,
            "reason": "Research finding requires review before updating knowledge.",
        })
        return {"agent": _public(agent), "finding": _public(finding), "approval": approval}

    def review_agent_findings(self, workspace_id: str, data: dict[str, Any]) -> dict[str, Any]:
        finding_id = _require_text(str(data.get("findingId") or ""), "findingId", 120)
        decision = str(data.get("decision") or data.get("status") or "").strip().lower()
        if decision not in {"accepted", "rejected", "archived", "pending_review"}:
            raise ValueError("decision must be accepted, rejected, archived, or pending_review")
        finding = self.client.get_document(_path(workspace_id, "agentFindings", finding_id))
        if not finding or finding.get("workspaceId") != workspace_id:
            raise LookupError("finding not found")
        finding["status"] = decision
        finding["reviewNotes"] = str(data.get("reviewNotes") or "")
        finding["reviewedAt"] = _now()
        finding["updatedAt"] = finding["reviewedAt"]
        self.client.set_document(_path(workspace_id, "agentFindings", finding_id), finding)
        return _public(finding)

    def approve_knowledge_update(self, workspace_id: str, data: dict[str, Any]) -> dict[str, Any]:
        finding_id = str(data.get("findingId") or "").strip()
        knowledge_id = str(data.get("knowledgeId") or "").strip()
        if finding_id:
            finding = self.client.get_document(_path(workspace_id, "agentFindings", finding_id))
            if not finding or finding.get("workspaceId") != workspace_id:
                raise LookupError("finding not found")
            proposed = dict(finding.get("proposedKnowledge") or {})
            proposed["findingId"] = finding_id
            proposed["agentId"] = finding.get("agentId")
            proposed["status"] = "accepted"
            proposed["approved"] = True
            knowledge = self.ingest_knowledge(workspace_id, proposed)["knowledgeItem"]
            finding["status"] = "accepted"
            finding["approvedKnowledgeId"] = knowledge["id"]
            finding["updatedAt"] = _now()
            self.client.set_document(_path(workspace_id, "agentFindings", finding_id), finding)
            return {"knowledgeItem": knowledge, "finding": _public(finding)}
        if knowledge_id:
            item = self.client.get_document(_path(workspace_id, "knowledgeItems", knowledge_id))
            if not item or item.get("workspaceId") != workspace_id:
                raise LookupError("knowledge not found")
            item["status"] = "accepted"
            item["approvedAt"] = _now()
            item["updatedAt"] = item["approvedAt"]
            self.client.set_document(_path(workspace_id, "knowledgeItems", knowledge_id), item)
            return {"knowledgeItem": _public(item)}
        raise ValueError("findingId or knowledgeId is required")

    def create_learning_approval(self, workspace_id: str, data: dict[str, Any]) -> dict[str, Any]:
        approval_id = _id("lapr")
        item = {
            "id": approval_id,
            "workspaceId": workspace_id,
            "targetType": str(data.get("targetType") or ""),
            "targetId": str(data.get("targetId") or ""),
            "reason": str(data.get("reason") or "Review required."),
            "status": "pending_review",
            "createdAt": _now(),
        }
        self.client.set_document(_path(workspace_id, "learningApprovals", approval_id), item)
        return _public(item)

    def _find_by_name(self, workspace_id: str, collection: str, name: str) -> dict[str, Any] | None:
        wanted = _slug(name)
        for item in self.client.list_documents(_collection(workspace_id, collection), limit=200):
            if item.get("workspaceId") == workspace_id and _slug(str(item.get("name") or "")) == wanted:
                return item
        return None


def _collection(workspace_id: str, collection: str) -> str:
    return f"workspaces/{workspace_id}/{collection}"


def _path(workspace_id: str, collection: str, item_id: str) -> str:
    if "/" in workspace_id or "/" in item_id:
        raise ValueError("invalid scoped id")
    return f"{_collection(workspace_id, collection)}/{item_id}"


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:16]}"


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _require_text(value: str, field: str, max_length: int) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{field} is required")
    if len(text) > max_length:
        raise ValueError(f"{field} must be {max_length} characters or fewer")
    return text


def _public(item: dict[str, Any]) -> dict[str, Any]:
    out = dict(item)
    out.pop("workspaceId", None)
    return out


def _words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9][a-z0-9_-]{2,}", text.lower())


def _slug(text: str) -> str:
    return "-".join(_words(text))


def _as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, tuple):
        return [str(item).strip() for item in value if str(item).strip()]
    text = str(value).strip()
    return [text] if text else []


def _merge_list(old: Any, new: Any) -> list[str]:
    merged: list[str] = []
    for value in [*_as_list(old), *_as_list(new)]:
        if value not in merged:
            merged.append(value)
    return merged


def _bounded_int(value: Any, lower: int, upper: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = lower
    return max(lower, min(parsed, upper))


def _clamp_float(value: Any, default: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        parsed = default
    return max(0.0, min(parsed, 1.0))


def _normalize_source_refs(value: Any) -> list[dict[str, Any]]:
    if not value:
        return []
    values = value if isinstance(value, list) else [value]
    refs = []
    for ref in values:
        if isinstance(ref, dict):
            item = {
                "type": str(ref.get("type") or "source"),
                "title": str(ref.get("title") or ref.get("name") or ""),
                "url": str(ref.get("url") or ""),
                "retrievedAt": str(ref.get("retrievedAt") or ref.get("retrievalDate") or _now()),
            }
        else:
            item = {"type": "source", "title": str(ref), "url": "", "retrievedAt": _now()}
        if item["title"] or item["url"]:
            refs.append(item)
    return refs


def _extract_people(text: str) -> list[str]:
    names: list[str] = []
    patterns = [
        r"(?:with|met|emailed|called|call with|talked to|spoke with|follow up with)\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)",
        r"([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)\s+(?:from|at)\s+[A-Z][A-Za-z0-9& ]{2,}",
    ]
    stop = {"Today", "Tomorrow", "Prepende", "OpenAI", "Anthropic", "NVIDIA"}
    for pattern in patterns:
        for match in re.findall(pattern, text):
            name = " ".join(match.split())
            if name and name not in stop and name not in names:
                names.append(name)
    return names[:12]


def _extract_projects(text: str) -> list[str]:
    projects: list[str] = []
    known = ("Prepende", "Workspace", "Project", "Research")
    for name in known:
        if re.search(rf"\b{re.escape(name)}\b", text, flags=re.IGNORECASE):
            projects.append(name)
    for match in re.findall(r"(?:project|initiative|workspace)\s+([A-Z][A-Za-z0-9_-]{2,}(?:\s+[A-Z][A-Za-z0-9_-]{2,})?)", text):
        name = " ".join(match.split())
        if name not in projects:
            projects.append(name)
    return projects[:12]


def _extract_topics(text: str) -> list[str]:
    keywords = {
        "memory": ("memory", "remember", "context", "engram"),
        "relationships": ("relationship", "person", "people", "follow up", "sentiment"),
        "projects": ("project", "roadmap", "status", "risk"),
        "knowledge": ("knowledge", "source", "claim", "research", "provenance"),
        "health": ("health", "sleep", "workout", "doctor", "stress"),
        "business": ("business", "market", "customer", "sales", "revenue"),
        "ai": ("openai", "anthropic", "local llm", "nvidia", "model"),
    }
    lower = text.lower()
    topics = [topic for topic, terms in keywords.items() if any(term in lower for term in terms)]
    return topics or ["personal_context"]


def _sentences(text: str) -> list[str]:
    return [part.strip() for part in re.split(r"(?<=[.!?])\s+", text) if part.strip()]


def _extract_commitments(text: str) -> list[str]:
    terms = ("i will", "i need to", "follow up", "commit", "promised", "tomorrow", "next week")
    return [s for s in _sentences(text) if any(term in s.lower() for term in terms)][:10]


def _extract_tasks(text: str) -> list[str]:
    terms = ("todo", "task", "need to", "follow up", "ship", "finish", "prepare")
    return [s for s in _sentences(text) if any(term in s.lower() for term in terms)][:10]


def _extract_goals(text: str) -> list[str]:
    terms = ("goal", "plan", "focus", "want to", "trying to")
    return [s for s in _sentences(text) if any(term in s.lower() for term in terms)][:10]


def _extract_durable_memories(text: str, people: list[str], projects: list[str], topics: list[str], commitments: list[str]) -> list[str]:
    memories = []
    if projects:
        memories.append(f"Projects mentioned: {', '.join(projects)}; topics: {', '.join(topics)}.")
    if people:
        memories.append(f"People mentioned: {', '.join(people)}.")
    memories.extend(commitments[:3])
    if not memories:
        memories.append(text[:500])
    return memories[:5]


def _journal_types(text: str) -> list[str]:
    lower = text.lower()
    mapping = {
        "daily_reflection": ("today", "reflection", "journal"),
        "personal_thought": ("i feel", "i think", "personal"),
        "business_idea": ("business", "idea", "market", "customer"),
        "health_update": ("health", "sleep", "doctor", "workout", "stress"),
        "emotional_context": ("felt", "feel", "anxious", "stressed", "excited", "sad"),
        "decision_note": ("decided", "decision", "choose", "chose"),
        "lesson_learned": ("learned", "lesson", "realized"),
        "future_plan": ("plan", "tomorrow", "next", "will"),
    }
    types = [name for name, terms in mapping.items() if any(term in lower for term in terms)]
    return types or ["personal_thought"]


def _emotional_tone(text: str) -> str:
    lower = text.lower()
    positive = sum(1 for term in ("excited", "happy", "clear", "good", "confident", "energized") if term in lower)
    negative = sum(1 for term in ("stressed", "sad", "anxious", "worried", "angry", "tired") if term in lower)
    if positive and negative:
        return "mixed"
    if positive:
        return "positive"
    if negative:
        return "stressed" if "stress" in lower or "stressed" in lower else "negative"
    return "neutral"
