# ruff: noqa: INP001
"""Slice 3 workflow-record parser and authorization tests."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from app.mission.assignment import derive_effective_assignments
from app.mission.authorization import authorize_record_author
from app.mission.github_client import (
    normalize_oauth_scopes,
    verify_exact_read_project_scope,
)
from app.mission.principal_registry import parse_principal_registry_json
from app.mission.types import QuarantineReason
from app.mission.validation import (
    CandidateRecord,
    CommentSourceMeta,
    check_edited_comment,
    check_exact_head,
    validate_supersession_graph,
    _would_create_cycle,
)
from app.mission.workflow_record import ParsedWorkflowRecord, parse_workflow_record_from_comment


REGISTRY_JSON = """
{
  "principals": [
    {
      "github_login": "human-owner-login",
      "trust_class": "human"
    },
    {
      "github_login": "reviewer-login",
      "trust_class": "reviewer"
    },
    {
      "github_login": "cursor-bot",
      "trust_class": "worker",
      "worker_identity": "cursor",
      "allowed_roles": ["technical-director", "gameplay-engineer"],
      "declarable_identities": ["cursor"]
    },
    {
      "github_login": "claude-bot",
      "trust_class": "worker",
      "worker_identity": "claude",
      "allowed_roles": ["technical-director"],
      "declarable_identities": ["claude"]
    }
  ]
}
"""


def _comment(body: str) -> str:
    return f"Prose\n\n{body}\n"


def _marker(payload: str) -> str:
    return f"<!-- ai-workflow-record:v1\n{payload}\n-->"


def test_parse_all_six_record_types() -> None:
    fixtures = {
        "start_task": (
            '{"type":"start_task","card":148,"worker":"cursor","role":"technical-director",'
            '"artifact":null,"head":null,"result":null,"supersedes":null}'
        ),
        "handoff": (
            '{"type":"handoff","card":148,"worker":"claude","role":"technical-director",'
            '"artifact":null,"head":null,"result":null,"supersedes":101}'
        ),
        "review_result": (
            '{"type":"review_result","card":148,"worker":"chatgpt-reviewer","role":"qa-reviewer",'
            '"artifact":"pr:self#1","head":"'
            + ("a" * 40)
            + '","result":"approved","supersedes":null}'
        ),
        "human_approval": (
            '{"type":"human_approval","card":148,"worker":"human","role":"human-owner",'
            '"artifact":"pr:self#1","head":"'
            + ("b" * 40)
            + '","result":null,"supersedes":null}'
        ),
        "kanban_update": (
            '{"type":"kanban_update","card":148,"worker":"cursor","role":"technical-director",'
            '"artifact":null,"head":null,"result":null,"supersedes":null}'
        ),
        "completion": (
            '{"type":"completion","card":148,"worker":"human","role":"human-owner",'
            '"artifact":null,"head":null,"result":null,"supersedes":null}'
        ),
    }
    for record_type, payload in fixtures.items():
        result = parse_workflow_record_from_comment(_comment(_marker(payload)))
        assert result.ok, (record_type, result.findings)
        assert result.record is not None
        assert result.record.type == record_type


def test_reject_malformed_duplicate_marker_and_unknown_fields() -> None:
    body = _comment(_marker('{"type":"start_task"}') + "\n" + _marker('{"type":"start_task"}'))
    result = parse_workflow_record_from_comment(body)
    assert not result.ok

    payload = (
        '{"type":"start_task","card":1,"worker":"cursor","role":"technical-director",'
        '"artifact":null,"head":null,"result":null,"supersedes":null,"extra":1}'
    )
    result2 = parse_workflow_record_from_comment(_comment(_marker(payload)))
    assert not result2.ok


def test_exact_read_project_scope() -> None:
    assert normalize_oauth_scopes("read:project") == frozenset({"read:project"})
    verify_exact_read_project_scope("read:project")
    with pytest.raises(RuntimeError, match="exactly"):
        verify_exact_read_project_scope("read:project,repo")
    with pytest.raises(RuntimeError, match="exactly"):
        verify_exact_read_project_scope("")


def test_author_authorization_and_unauthorized() -> None:
    registry = parse_principal_registry_json(REGISTRY_JSON)
    payload = (
        '{"type":"start_task","card":148,"worker":"cursor","role":"technical-director",'
        '"artifact":null,"head":null,"result":null,"supersedes":null}'
    )
    parsed = parse_workflow_record_from_comment(_comment(_marker(payload)))
    assert parsed.ok and parsed.record is not None

    ok = authorize_record_author(
        record=parsed.record,
        github_login="cursor-bot",
        registry=registry,
    )
    assert ok.ok

    bad = authorize_record_author(
        record=parsed.record,
        github_login="stranger",
        registry=registry,
    )
    assert not bad.ok
    assert bad.reason == QuarantineReason.UNAUTHORIZED_AUTHOR


def test_edited_comment_quarantine() -> None:
    created = datetime(2026, 7, 21, 1, 0, 0)
    source = CommentSourceMeta(
        comment_id=1,
        card=148,
        github_login="cursor-bot",
        created_at=created,
        updated_at=created + timedelta(seconds=1),
        html_url="https://example.com",
        body="x",
        on_authoritative_issue=True,
        issue_number=148,
    )
    issue = check_edited_comment(source)
    assert issue is not None
    assert issue.reason == QuarantineReason.EDITED_COMMENT


def test_stale_head_quarantine() -> None:
    payload = (
        '{"type":"review_result","card":148,"worker":"chatgpt-reviewer","role":"qa-reviewer",'
        '"artifact":"pr:self#9","head":"'
        + ("a" * 40)
        + '","result":"approved","supersedes":null}'
    )
    parsed = parse_workflow_record_from_comment(_comment(_marker(payload)))
    assert parsed.record is not None
    issue = check_exact_head(parsed.record, current_head="b" * 40)
    assert issue is not None
    assert issue.reason == QuarantineReason.STALE_HEAD


def _candidate(
    comment_id: int,
    payload: str,
    *,
    created: datetime,
    login: str = "cursor-bot",
) -> CandidateRecord:
    parsed = parse_workflow_record_from_comment(_comment(_marker(payload)))
    assert parsed.record is not None
    source = CommentSourceMeta(
        comment_id=comment_id,
        card=parsed.record.card,
        github_login=login,
        created_at=created,
        updated_at=created,
        html_url=f"https://example.com/{comment_id}",
        body=_comment(_marker(payload)),
        on_authoritative_issue=True,
        issue_number=parsed.record.card,
    )
    return CandidateRecord(source=source, record=parsed.record)


def test_supersession_duplicate_and_cross_card() -> None:
    t0 = datetime(2026, 7, 21, 1, 0, 0)
    a = _candidate(
        1,
        '{"type":"start_task","card":10,"worker":"cursor","role":"technical-director",'
        '"artifact":null,"head":null,"result":null,"supersedes":null}',
        created=t0,
    )
    b = _candidate(
        2,
        '{"type":"handoff","card":10,"worker":"claude","role":"technical-director",'
        '"artifact":null,"head":null,"result":null,"supersedes":1}',
        created=t0 + timedelta(minutes=1),
    )
    c = _candidate(
        3,
        '{"type":"handoff","card":10,"worker":"cursor","role":"technical-director",'
        '"artifact":null,"head":null,"result":null,"supersedes":1}',
        created=t0 + timedelta(minutes=2),
    )
    issues = validate_supersession_graph([a, b, c])
    assert 3 in issues
    assert issues[3].reason == QuarantineReason.DUPLICATE_SUPERSESSION

    other = _candidate(
        4,
        '{"type":"handoff","card":99,"worker":"claude","role":"technical-director",'
        '"artifact":null,"head":null,"result":null,"supersedes":1}',
        created=t0 + timedelta(minutes=3),
    )
    cross = validate_supersession_graph([a, other])
    assert 4 in cross
    assert cross[4].reason == QuarantineReason.CROSS_CARD_SUPERSESSION


def test_cyclic_supersession_detected() -> None:
    """A->B and B->A: validating A detects a cycle via walk-from-target."""
    t0 = datetime(2026, 7, 21, 4, 0, 0)

    def make(cid: int, supersedes: int | None, created: datetime) -> CandidateRecord:
        record = ParsedWorkflowRecord(
            type="handoff",
            card=40,
            worker="claude",
            role="technical-director",
            artifact=None,
            head=None,
            result=None,
            supersedes=supersedes,
            artifact_parsed=None,
        )
        source = CommentSourceMeta(
            comment_id=cid,
            card=40,
            github_login="cursor-bot",
            created_at=created,
            updated_at=created,
            html_url=f"https://example.com/{cid}",
            body="",
            on_authoritative_issue=True,
            issue_number=40,
        )
        return CandidateRecord(source=source, record=record)

    a = make(51, 52, t0 + timedelta(minutes=1))
    b = make(52, 51, t0)
    by_id = {51: a, 52: b}
    assert _would_create_cycle(by_id, 51, 52) is True


def test_conflicting_assignment_quarantine() -> None:
    t0 = datetime(2026, 7, 21, 2, 0, 0)
    a = _candidate(
        21,
        '{"type":"start_task","card":20,"worker":"cursor","role":"technical-director",'
        '"artifact":null,"head":null,"result":null,"supersedes":null}',
        created=t0,
    )
    b = _candidate(
        22,
        '{"type":"start_task","card":20,"worker":"claude","role":"technical-director",'
        '"artifact":null,"head":null,"result":null,"supersedes":null}',
        created=t0 + timedelta(minutes=1),
        login="claude-bot",
    )
    derivation = derive_effective_assignments([a, b], quarantined_ids=set())
    assert 20 in derivation.conflicts
    assert derivation.conflicts[20].reason == QuarantineReason.CONFLICTING_ASSIGNMENT
    assert 20 not in derivation.effective


def test_effective_assignment_latest_unsuperseded() -> None:
    t0 = datetime(2026, 7, 21, 3, 0, 0)
    a = _candidate(
        31,
        '{"type":"start_task","card":30,"worker":"cursor","role":"technical-director",'
        '"artifact":null,"head":null,"result":null,"supersedes":null}',
        created=t0,
    )
    b = _candidate(
        32,
        '{"type":"handoff","card":30,"worker":"claude","role":"technical-director",'
        '"artifact":null,"head":null,"result":null,"supersedes":31}',
        created=t0 + timedelta(minutes=1),
    )
    derivation = derive_effective_assignments([a, b], quarantined_ids=set())
    assert derivation.effective[30].worker == "claude"
