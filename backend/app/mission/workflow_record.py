"""ai-workflow-record:v1 parser (payload-local structural validation).

Ports the closed Slice 2 contract into Mission Control for adapter-time use.
Principal authorization, exact-head, supersession, and assignment derivation
are handled by sibling modules — not here.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Literal

from app.mission.role_slugs import ROLE_SLUGS
from app.mission.types import (
    ASSIGNMENT_WORKER_IDS,
    RECORD_FIELDS,
    RECORD_TYPES,
    REPO_QUALIFIERS,
    REVIEW_RESULTS,
    ROLE_HUMAN_OWNER,
    ROLE_QA_REVIEWER,
    WORKER_IDS,
)

FULL_COMMIT_SHA = re.compile(r"^[0-9a-f]{40}$")
MARKER_PATTERN = re.compile(r"<!--\s*ai-workflow-record:v1\b([\s\S]*?)-->", re.MULTILINE)


@dataclass(frozen=True, slots=True)
class Finding:
    rule_id: str
    message: str


@dataclass(frozen=True, slots=True)
class ArtifactRefParsed:
    kind: Literal["pr", "issue", "path"]
    qualifier: Literal["self", "mission-control"]
    value: str


@dataclass(frozen=True, slots=True)
class ParsedWorkflowRecord:
    type: str
    card: int
    worker: str | None
    role: str | None
    artifact: str | None
    head: str | None
    result: str | None
    supersedes: int | None
    artifact_parsed: ArtifactRefParsed | None


@dataclass(frozen=True, slots=True)
class ParseResult:
    ok: bool
    record: ParsedWorkflowRecord | None
    findings: tuple[Finding, ...]
    payload_text: str | None = None


def extract_markers(comment_body: str) -> list[str]:
    """Return every marker payload (trimmed) from a comment body."""
    return [match.group(1).strip() for match in MARKER_PATTERN.finditer(comment_body)]


def find_duplicate_object_key(source: str) -> str | None:
    """Detect duplicate JSON object member names before json.loads."""

    def skip_ws(index: int) -> int:
        while index < len(source) and source[index].isspace():
            index += 1
        return index

    def scan_string(index: int) -> tuple[str | None, int, str | None]:
        if index >= len(source) or source[index] != '"':
            return None, index, f"Expected string at index {index}"
        index += 1
        chars: list[str] = []
        while index < len(source):
            ch = source[index]
            if ch == '"':
                return "".join(chars), index + 1, None
            if ch == "\\":
                if index + 1 >= len(source):
                    return None, index, "Unterminated escape in JSON string"
                chars.append(source[index + 1])
                index += 2
                continue
            chars.append(ch)
            index += 1
        return None, index, "Unterminated JSON string"

    def scan_value(start: int) -> tuple[int, str | None, str | None]:
        index = skip_ws(start)
        if index >= len(source):
            return index, None, "Unexpected end of JSON"
        ch = source[index]
        if ch == "{":
            return scan_object(index + 1)
        if ch == "[":
            return scan_array(index + 1)
        if ch == '"':
            _value, next_index, err = scan_string(index)
            return next_index, None, err
        if source.startswith("true", index):
            return index + 4, None, None
        if source.startswith("false", index):
            return index + 5, None, None
        if source.startswith("null", index):
            return index + 4, None, None
        if ch in "-0123456789":
            match = re.match(r"-?(?:0|[1-9]\d*)(?:\.\d+)?(?:[eE][+-]?\d+)?", source[index:])
            if not match:
                return index, None, f"Invalid number at index {index}"
            return index + len(match.group(0)), None, None
        return index, None, f"Unexpected token at index {index}"

    def scan_object(start: int) -> tuple[int, str | None, str | None]:
        seen: set[str] = set()
        index = skip_ws(start)
        if index < len(source) and source[index] == "}":
            return index + 1, None, None
        while index < len(source):
            index = skip_ws(index)
            key, index, err = scan_string(index)
            if err or key is None:
                return index, None, err
            if key in seen:
                return index, key, None
            seen.add(key)
            index = skip_ws(index)
            if index >= len(source) or source[index] != ":":
                return index, None, f"Expected ':' after key at index {index}"
            index, dup, err = scan_value(index + 1)
            if dup or err:
                return index, dup, err
            index = skip_ws(index)
            if index < len(source) and source[index] == "}":
                return index + 1, None, None
            if index >= len(source) or source[index] != ",":
                return index, None, f"Expected ',' or '}}' at index {index}"
            index += 1
        return index, None, "Unterminated JSON object"

    def scan_array(start: int) -> tuple[int, str | None, str | None]:
        index = skip_ws(start)
        if index < len(source) and source[index] == "]":
            return index + 1, None, None
        while index < len(source):
            index, dup, err = scan_value(index)
            if dup or err:
                return index, dup, err
            index = skip_ws(index)
            if index < len(source) and source[index] == "]":
                return index + 1, None, None
            if index >= len(source) or source[index] != ",":
                return index, None, f"Expected ',' or ']' at index {index}"
            index += 1
        return index, None, "Unterminated JSON array"

    _index, duplicate, _err = scan_value(0)
    return duplicate


def parse_artifact_ref(value: str) -> tuple[ArtifactRefParsed | None, str | None]:
    match = re.fullmatch(r"(pr|issue|path):([^#]+)#(.+)", value)
    if match is None:
        return None, (
            "artifact must use repository-qualified form kind:qualifier#value "
            "(unqualified forms are rejected)"
        )
    kind, qualifier, raw_value = match.group(1), match.group(2), match.group(3)
    if qualifier not in REPO_QUALIFIERS:
        return None, (
            f"artifact RepoQualifier must be one of "
            f"{' | '.join(sorted(REPO_QUALIFIERS))}; got: {qualifier}"
        )
    if kind in {"pr", "issue"}:
        if not re.fullmatch(r"[1-9]\d*", raw_value):
            return None, f"artifact {kind}: value must be a positive integer"
        return (
            ArtifactRefParsed(
                kind=kind,  # type: ignore[arg-type]
                qualifier=qualifier,  # type: ignore[arg-type]
                value=raw_value,
            ),
            None,
        )

    if raw_value == "":
        return None, "artifact path: value must be a non-empty repository-relative path"
    if (
        raw_value.startswith("/")
        or raw_value.startswith("\\")
        or re.match(r"^[A-Za-z]:[\\/]", raw_value) is not None
    ):
        return None, "artifact path: rejects absolute paths"
    if "\\" in raw_value:
        return None, "artifact path: rejects backslashes"
    if (
        re.search(r"%2e", raw_value, re.I)
        or re.search(r"%2f", raw_value, re.I)
        or re.search(r"%5c", raw_value, re.I)
    ):
        return None, "artifact path: rejects percent-encoded traversal"
    segments = raw_value.split("/")
    if any(segment == "" or segment in {".", ".."} for segment in segments):
        return None, "artifact path: rejects empty, '.', or '..' segments"
    return (
        ArtifactRefParsed(kind="path", qualifier=qualifier, value=raw_value),  # type: ignore[arg-type]
        None,
    )


def parse_workflow_record_from_comment(comment_body: str) -> ParseResult:
    """Parse exactly one ai-workflow-record:v1 marker from a comment body."""
    if not isinstance(comment_body, str):
        return ParseResult(
            ok=False,
            record=None,
            findings=(Finding("record.extract", "comment body must be a string"),),
        )

    markers = extract_markers(comment_body)
    if len(markers) == 0:
        return ParseResult(
            ok=False,
            record=None,
            findings=(
                Finding(
                    "record.extract",
                    "comment must contain exactly one ai-workflow-record:v1 marker",
                ),
            ),
        )
    if len(markers) > 1:
        return ParseResult(
            ok=False,
            record=None,
            findings=(
                Finding(
                    "record.extract",
                    "comment must contain exactly one ai-workflow-record:v1 marker",
                ),
            ),
            payload_text=markers[0],
        )
    return parse_workflow_record_payload(markers[0])


def parse_workflow_record_payload(payload_text: str) -> ParseResult:
    """Structurally validate one marker payload text."""
    findings: list[Finding] = []
    duplicate = find_duplicate_object_key(payload_text)
    if duplicate is not None:
        return ParseResult(
            ok=False,
            record=None,
            findings=(
                Finding(
                    "record.duplicate_key",
                    f"payload contains duplicate JSON member name: {duplicate}",
                ),
            ),
            payload_text=payload_text,
        )

    try:
        data: Any = json.loads(payload_text)
    except json.JSONDecodeError as exc:
        return ParseResult(
            ok=False,
            record=None,
            findings=(Finding("record.json", f"malformed JSON: {exc.msg}"),),
            payload_text=payload_text,
        )

    if not isinstance(data, dict):
        return ParseResult(
            ok=False,
            record=None,
            findings=(Finding("record.shape", "payload must be a JSON object"),),
            payload_text=payload_text,
        )

    keys = set(data.keys())
    expected = set(RECORD_FIELDS)
    missing = expected - keys
    extra = keys - expected
    if missing:
        findings.append(Finding("record.fields", f"missing required fields: {sorted(missing)}"))
    if extra:
        findings.append(
            Finding("record.fields", f"unknown fields are not allowed: {sorted(extra)}")
        )

    record_type = data.get("type")
    if record_type not in RECORD_TYPES:
        findings.append(
            Finding(
                "record.type",
                f"type must be one of {sorted(RECORD_TYPES)}; got: {record_type!r}",
            )
        )

    card = data.get("card")
    if not _is_positive_int(card):
        findings.append(Finding("record.card", "card must be a positive integer"))

    worker = data.get("worker")
    if worker is not None and worker not in WORKER_IDS:
        findings.append(
            Finding(
                "record.worker",
                f"worker must be one of {sorted(WORKER_IDS)} or null; got: {worker!r}",
            )
        )

    role = data.get("role")
    if role is not None:
        if not isinstance(role, str):
            findings.append(Finding("record.role", "role must be a string RoleSlug or null"))
        elif role not in ROLE_SLUGS:
            findings.append(
                Finding("record.role", f"role is not in the closed RoleSlug registry: {role}")
            )

    artifact = data.get("artifact")
    artifact_parsed: ArtifactRefParsed | None = None
    if artifact is not None:
        if not isinstance(artifact, str):
            findings.append(Finding("record.artifact", "artifact must be a string or null"))
        else:
            artifact_parsed, artifact_err = parse_artifact_ref(artifact)
            if artifact_err is not None:
                findings.append(Finding("record.artifact", artifact_err))

    head = data.get("head")
    if head is not None and not (isinstance(head, str) and FULL_COMMIT_SHA.fullmatch(head)):
        findings.append(
            Finding(
                "record.head",
                "head must be a full 40-character lowercase commit SHA or null",
            )
        )

    result = data.get("result")
    if result is not None and result not in REVIEW_RESULTS:
        findings.append(
            Finding(
                "record.result",
                f"result must be one of {sorted(REVIEW_RESULTS)} or null; got: {result!r}",
            )
        )

    supersedes = data.get("supersedes")
    if supersedes is not None and not _is_positive_int(supersedes):
        findings.append(
            Finding(
                "record.supersedes",
                "supersedes must be a positive GitHub comment id or null",
            )
        )

    if record_type in RECORD_TYPES and not findings:
        _validate_nullability(data, findings)

    if findings:
        return ParseResult(
            ok=False,
            record=None,
            findings=tuple(findings),
            payload_text=payload_text,
        )

    assert isinstance(card, int)
    assert isinstance(record_type, str)
    return ParseResult(
        ok=True,
        record=ParsedWorkflowRecord(
            type=record_type,
            card=card,
            worker=worker if isinstance(worker, str) else None,
            role=role if isinstance(role, str) else None,
            artifact=artifact if isinstance(artifact, str) else None,
            head=head if isinstance(head, str) else None,
            result=result if isinstance(result, str) else None,
            supersedes=supersedes if isinstance(supersedes, int) else None,
            artifact_parsed=artifact_parsed,
        ),
        findings=(),
        payload_text=payload_text,
    )


def _is_positive_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 1


def _validate_nullability(record: dict[str, Any], findings: list[Finding]) -> None:
    record_type = record["type"]

    def expect_null(field: str) -> None:
        if record[field] is not None:
            findings.append(
                Finding("record.nullability", f"{record_type} requires {field} to be null")
            )

    def expect_present(field: str) -> bool:
        if record[field] is None:
            findings.append(
                Finding(
                    "record.nullability",
                    f"{record_type} requires {field} to be present (non-null)",
                )
            )
            return False
        return True

    if record_type == "start_task":
        if expect_present("worker") and record["worker"] not in ASSIGNMENT_WORKER_IDS:
            findings.append(
                Finding(
                    "record.worker",
                    "start_task worker must be a Worker-class identity "
                    "(never human or chatgpt-reviewer)",
                )
            )
        expect_present("role")
        expect_null("artifact")
        expect_null("head")
        expect_null("result")
        if record["supersedes"] is not None and not _is_positive_int(record["supersedes"]):
            findings.append(
                Finding(
                    "record.field",
                    "start_task supersedes must be a positive GitHub comment id or null",
                )
            )
    elif record_type == "handoff":
        if expect_present("worker") and record["worker"] not in ASSIGNMENT_WORKER_IDS:
            findings.append(
                Finding(
                    "record.worker",
                    "handoff worker must be a Worker-class identity "
                    "(never human or chatgpt-reviewer)",
                )
            )
        expect_present("role")
        expect_null("artifact")
        expect_null("head")
        expect_null("result")
        if not expect_present("supersedes") or not _is_positive_int(record["supersedes"]):
            findings.append(
                Finding(
                    "record.field",
                    "handoff supersedes must be a positive GitHub comment id",
                )
            )
    elif record_type == "review_result":
        if expect_present("worker") and record["worker"] != "chatgpt-reviewer":
            findings.append(
                Finding("record.worker", "review_result worker must be chatgpt-reviewer")
            )
        if expect_present("role") and record["role"] != ROLE_QA_REVIEWER:
            findings.append(
                Finding("record.role", "review_result role must be exactly qa-reviewer")
            )
        if expect_present("artifact"):
            parsed, err = parse_artifact_ref(str(record["artifact"]))
            if err is not None:
                findings.append(Finding("record.artifact", err))
            elif parsed is not None and parsed.kind == "issue":
                findings.append(
                    Finding(
                        "record.artifact",
                        "review_result artifact must be pr: or path: (never issue:)",
                    )
                )
        if expect_present("head") and not (
            isinstance(record["head"], str) and FULL_COMMIT_SHA.fullmatch(record["head"])
        ):
            findings.append(
                Finding(
                    "record.head",
                    "review_result head must be a full 40-character lowercase commit SHA",
                )
            )
        if expect_present("result") and record["result"] not in REVIEW_RESULTS:
            findings.append(
                Finding(
                    "record.result",
                    f"review_result result must be one of {' | '.join(sorted(REVIEW_RESULTS))}",
                )
            )
        if record["supersedes"] is not None and not _is_positive_int(record["supersedes"]):
            findings.append(
                Finding(
                    "record.field",
                    "review_result supersedes must be a positive GitHub comment id or null",
                )
            )
    elif record_type == "human_approval":
        if expect_present("worker") and record["worker"] != "human":
            findings.append(Finding("record.worker", "human_approval worker must be human"))
        if expect_present("role") and record["role"] != ROLE_HUMAN_OWNER:
            findings.append(
                Finding("record.role", "human_approval role must be exactly human-owner")
            )
        if expect_present("artifact"):
            parsed, err = parse_artifact_ref(str(record["artifact"]))
            if err is not None:
                findings.append(Finding("record.artifact", err))
            elif parsed is not None and parsed.kind == "issue":
                findings.append(
                    Finding(
                        "record.artifact",
                        "human_approval artifact must be pr: or path: (never issue:)",
                    )
                )
        if expect_present("head") and not (
            isinstance(record["head"], str) and FULL_COMMIT_SHA.fullmatch(record["head"])
        ):
            findings.append(
                Finding(
                    "record.head",
                    "human_approval head must be a full 40-character lowercase commit SHA",
                )
            )
        expect_null("result")
        if record["supersedes"] is not None and not _is_positive_int(record["supersedes"]):
            findings.append(
                Finding(
                    "record.field",
                    "human_approval supersedes must be a positive GitHub comment id or null",
                )
            )
    elif record_type in {"kanban_update", "completion"}:
        if expect_present("worker") and record["worker"] not in (ASSIGNMENT_WORKER_IDS | {"human"}):
            findings.append(
                Finding(
                    "record.worker",
                    f"{record_type} worker must be the effective Worker identity or human",
                )
            )
        expect_present("role")
        expect_null("artifact")
        expect_null("head")
        expect_null("result")
        if record["supersedes"] is not None and not _is_positive_int(record["supersedes"]):
            findings.append(
                Finding(
                    "record.field",
                    f"{record_type} supersedes must be a positive GitHub comment id or null",
                )
            )
    else:
        findings.append(Finding("record.type", f"unhandled record type: {record_type}"))
