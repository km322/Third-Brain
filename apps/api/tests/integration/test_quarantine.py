"""Integration: the secret-scan quarantine lifecycle over the real stack.

Content that appears to contain credentials is quarantined BEFORE indexing: no chunks
are written, retrieval never surfaces it, and the review endpoint shows WHAT was
detected (redacted), WHICH collection it targets and WHO could read it. An editor can
approve ("index anyway", keyed to the content checksum) or discard the document, and
the MCP write tools apply the same scan synchronously. Raw secret material must never
appear in any API response - only redacted samples.

All planted secrets are realistic but harmless dummies (AWS's documented example
access key id, a fabricated RSA block, a made-up GitHub token shape).

The file walks that lifecycle in order: helpers, detection, review (findings, target
collection, audience redaction), the refusal to reprocess while quarantined, approve,
discard, retrieval defense-in-depth, clean content, then the MCP write tools.
"""

from __future__ import annotations

import json
import uuid

import factories
import pytest
from sqlalchemy import func, select

from app.models.audit import AuditLog
from app.models.chunk import DocumentChunk
from app.models.document import Document
from app.models.enums import DocumentStatus, OrgRole, PermissionLevel, ResourceType, Visibility

pytestmark = pytest.mark.integration

_AWS_KEY = "AKIAIOSFODNN7EXAMPLE"
_AWS_SECRET_TEXT = (
    "Deployment runbook for the object storage gateway. Rotate service credentials "
    "every quarter and record the change in the operations log. The current gateway "
    f"access key id is {_AWS_KEY} and it must never be committed to source control. "
    "Page the infrastructure on-call channel if a rotation fails."
)
_AWS_QUERY = "object storage gateway credential rotation runbook"

_CLEAN_TEXT = (
    "Retrieval augmented generation grounds a language model in an organization's own "
    "documents so answers stay accurate, current and attributable to a source."
)

_MIGRATION_TEXT = (
    "The billing migration playbook moves customer invoices to the new ledger region "
    "during a weekend maintenance window, with a rollback checkpoint every hour."
)
_MIGRATION_QUERY = "billing migration playbook ledger region"

_PRIVATE_KEY_MARKER = "MIIBOgIBAAJBFAKE"
_PRIVATE_KEY_TEXT = (
    "Legacy VPN bootstrap notes. The appliance still trusts the key below until the "
    "certificate migration completes.\n"
    "-----BEGIN RSA PRIVATE KEY-----\n"
    f"{_PRIVATE_KEY_MARKER}fakeFAKEfakeFAKEfakeFAKEfakeFAKEfakeFAKEfakeFAKE\n"
    "fakeFAKEfakeFAKEfakeFAKEfakeFAKEfakeFAKEfakeFAKEfakeFAKEfakeFAKE\n"
    "-----END RSA PRIVATE KEY-----\n"
    "File a change ticket before rotating the appliance credentials."
)

_GITHUB_TOKEN = "ghp_Zx91Qw82Er73Ty64Ui55Op46As37Df28Gh19"
_CLEAN_MCP_TEXT = (
    "Quarterly onboarding checklist for new analysts covering tooling access, "
    "mentorship pairings and the first-week reading list."
)
_CLEAN_MCP_QUERY = "onboarding checklist analysts mentorship"
_FLAGGED_UPDATE_TEXT = (
    "Revised onboarding checklist. Use the temporary automation token "
    f"{_GITHUB_TOKEN} to bootstrap the repository mirror, then revoke it."
)


async def _rpc(client, *, method, params=None, msg_id=1, headers=None):
    body = {"jsonrpc": "2.0", "id": msg_id, "method": method}
    if params is not None:
        body["params"] = params
    resp = await client.post("/mcp", json=body, headers=headers or {})
    assert resp.status_code == 200, resp.text
    return resp.json()


async def _org_with_collection(db_session):
    """An org owner plus an ORG-visible collection (default VIEWER for every member)."""
    org, owner, _ = await factories.create_org_with_owner(db_session)
    collection = await factories.create_collection(
        db_session,
        org=org,
        owner=owner,
        visibility=Visibility.ORG,
        default_permission=PermissionLevel.VIEWER,
    )
    return org, owner, collection


async def _upload_and_ingest(
    client, api, headers, ingest_now, collection, *, title, content, visibility=None
):
    payload = {"collection_id": str(collection.id), "title": title, "content": content}
    if visibility is not None:
        payload["visibility"] = visibility
    created = await client.post(f"{api}/documents/text", headers=headers, json=payload)
    assert created.status_code == 201, created.text
    document_id = created.json()["id"]
    await ingest_now(document_id)
    return document_id


async def _detail(client, api, headers, document_id) -> dict:
    resp = await client.get(f"{api}/documents/{document_id}", headers=headers)
    assert resp.status_code == 200, resp.text
    return resp.json()


async def _quarantined_document(client, api, headers, ingest_now, collection) -> str:
    """Upload the AWS-flagged text and drive it into QUARANTINED; return the doc id."""
    document_id = await _upload_and_ingest(
        client,
        api,
        headers,
        ingest_now,
        collection,
        title="Gateway runbook",
        content=_AWS_SECRET_TEXT,
    )
    detail = await _detail(client, api, headers, document_id)
    assert detail["status"] == "quarantined", detail
    return document_id


async def _search_doc_ids(client, api, headers, query: str) -> set[str]:
    resp = await client.post(f"{api}/search", headers=headers, json={"query": query})
    assert resp.status_code == 200, resp.text
    return {h["document_id"] for h in resp.json()["hits"]}


async def _chunk_rows(db_session, document_id) -> int:
    return (
        await db_session.execute(
            select(func.count())
            .select_from(DocumentChunk)
            .where(DocumentChunk.document_id == uuid.UUID(str(document_id)))
        )
    ).scalar()


async def _audit_rows(db_session, org_id, action: str, document_id) -> int:
    return (
        await db_session.execute(
            select(func.count())
            .select_from(AuditLog)
            .where(
                AuditLog.org_id == org_id,
                AuditLog.action == action,
                AuditLog.resource_id == str(document_id),
            )
        )
    ).scalar()


async def test_flagged_upload_is_quarantined_with_no_chunks_and_no_retrieval(
    client, db_session, token_headers, api, ingest_now
) -> None:
    """Flagged content quarantines before indexing.

    The raw key must never appear anywhere in the API response, redaction only, and
    nothing was indexed: zero chunk rows and zero retrieval hits.
    """
    org, owner, collection = await _org_with_collection(db_session)
    headers = token_headers(owner.id, org.id)

    document_id = await _upload_and_ingest(
        client,
        api,
        headers,
        ingest_now,
        collection,
        title="Gateway runbook",
        content=_AWS_SECRET_TEXT,
    )

    detail_resp = await client.get(f"{api}/documents/{document_id}", headers=headers)
    assert detail_resp.status_code == 200, detail_resp.text
    detail = detail_resp.json()
    assert detail["status"] == "quarantined"
    assert detail["chunk_count"] == 0
    assert detail.get("error") is None
    assert _AWS_KEY not in detail_resp.text

    assert await _chunk_rows(db_session, document_id) == 0
    hits = await _search_doc_ids(client, api, headers, _AWS_QUERY)
    assert document_id not in hits


async def test_review_returns_redacted_findings_collection_and_audience(
    client, db_session, token_headers, api, ingest_now
) -> None:
    """The review payload: redacted findings, the target collection and the audience.

    The document was uploaded without a document-level visibility override, so it
    inherits the collection's. The owner (org owner + collection manager) sees full
    identities, and the permission counts are exact: one manager (the owner) and one
    viewer (the plain member). The full secret appears nowhere in the payload.
    """
    org, owner, collection = await _org_with_collection(db_session)
    member, _ = await factories.add_member(
        db_session, org=org, role=OrgRole.VIEWER, full_name="Org Member"
    )
    headers = token_headers(owner.id, org.id)
    document_id = await _quarantined_document(client, api, headers, ingest_now, collection)

    resp = await client.get(f"{api}/documents/{document_id}/review", headers=headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert set(body) == {
        "document",
        "findings",
        "scanned_at",
        "truncated",
        "collection",
        "audience",
    }

    doc_block = body["document"]
    assert set(doc_block) == {
        "id",
        "title",
        "status",
        "source_type",
        "mime_type",
        "size_bytes",
        "created_at",
        "visibility",
    }
    assert doc_block["id"] == document_id
    assert doc_block["title"] == "Gateway runbook"
    assert doc_block["status"] == "quarantined"
    assert doc_block["visibility"] is None

    finding = next(f for f in body["findings"] if f["detector"] == "aws-access-key-id")
    assert set(finding) == {"detector", "label", "severity", "occurrences", "samples"}
    assert finding["severity"] == "high"
    assert finding["occurrences"] >= 1
    assert finding["samples"]
    sample = finding["samples"][0]
    assert set(sample) == {"redacted", "line"}
    assert sample["redacted"] == _AWS_KEY[:4] + "…" + _AWS_KEY[-2:]
    assert sample["line"] >= 1

    assert isinstance(body["scanned_at"], str) and body["scanned_at"]
    assert body["truncated"] is False

    assert body["collection"] == {
        "id": str(collection.id),
        "name": collection.name,
        "visibility": "org",
        "default_permission": "viewer",
    }

    audience = body["audience"]
    assert set(audience) == {"total_users", "truncated", "note", "permission_counts", "entries"}
    assert audience["total_users"] >= 2
    assert audience["truncated"] is False
    assert audience["note"] is None
    owner_entry = next(e for e in audience["entries"] if e["user_id"] == str(owner.id))
    assert set(owner_entry) == {"user_id", "name", "email", "permission", "via"}
    assert owner_entry["permission"] == "manager"
    assert owner_entry["email"] == owner.email
    member_entry = next(e for e in audience["entries"] if e["user_id"] == str(member.id))
    assert member_entry["permission"] == "viewer"
    assert member_entry["via"] == "visibility:org"

    counts = audience["permission_counts"]
    assert set(counts) == {"viewer", "editor", "manager"}
    assert counts["manager"] >= 1
    assert counts["viewer"] >= 1
    assert counts["viewer"] + counts["editor"] + counts["manager"] == audience["total_users"]

    assert _AWS_KEY not in resp.text


async def test_review_requires_editor_and_quarantined_status(
    client, db_session, token_headers, api, ingest_now
) -> None:
    """Who may review, and in which state.

    A member with only VIEWER on the collection (the ORG-visibility baseline) is 403. A
    user in another org cannot even see the document: 404, never 403. And review of a
    non-quarantined document is a conflict.
    """
    org, owner, collection = await _org_with_collection(db_session)
    owner_headers = token_headers(owner.id, org.id)
    document_id = await _quarantined_document(client, api, owner_headers, ingest_now, collection)

    viewer, _ = await factories.add_member(db_session, org=org, role=OrgRole.VIEWER)
    denied = await client.get(
        f"{api}/documents/{document_id}/review", headers=token_headers(viewer.id, org.id)
    )
    assert denied.status_code == 403, denied.text

    other_org, other_owner, _ = await factories.create_org_with_owner(db_session)
    unseen = await client.get(
        f"{api}/documents/{document_id}/review",
        headers=token_headers(other_owner.id, other_org.id),
    )
    assert unseen.status_code == 404, unseen.text

    clean_id = await _upload_and_ingest(
        client, api, owner_headers, ingest_now, collection, title="Primer", content=_CLEAN_TEXT
    )
    assert (await _detail(client, api, owner_headers, clean_id))["status"] == "indexed"
    conflict = await client.get(f"{api}/documents/{clean_id}/review", headers=owner_headers)
    assert conflict.status_code == 409, conflict.text


async def test_review_audience_redacts_identities_for_non_manager_editor(
    client, db_session, token_headers, api, ingest_now
) -> None:
    """The audience carries names + emails, so per-user identities are shown only to
    collection managers and org admins; a plain editor sees the aggregate counts only.

    An editor (collection EDITOR grant, cannot manage) may review but not see identities;
    the aggregate data is still exact - one manager (the owner) and one editor (this
    editor) - and the note explains the redaction. A manager (collection MANAGER grant)
    sees the full identity list.
    """
    org, owner, collection = await _org_with_collection(db_session)
    owner_headers = token_headers(owner.id, org.id)
    document_id = await _quarantined_document(client, api, owner_headers, ingest_now, collection)

    editor, _ = await factories.add_member(db_session, org=org, role=OrgRole.VIEWER)
    await factories.grant_user(
        db_session,
        org=org,
        user=editor,
        resource_type=ResourceType.COLLECTION,
        resource_id=collection.id,
        permission=PermissionLevel.EDITOR,
    )
    resp = await client.get(
        f"{api}/documents/{document_id}/review", headers=token_headers(editor.id, org.id)
    )
    assert resp.status_code == 200, resp.text
    audience = resp.json()["audience"]
    assert audience["entries"] == []
    counts = audience["permission_counts"]
    assert counts["manager"] >= 1
    assert counts["editor"] >= 1
    assert counts["viewer"] + counts["editor"] + counts["manager"] == audience["total_users"]
    assert audience["total_users"] >= 2
    assert "manager" in (audience["note"] or "").lower()

    manager, _ = await factories.add_member(db_session, org=org, role=OrgRole.VIEWER)
    await factories.grant_user(
        db_session,
        org=org,
        user=manager,
        resource_type=ResourceType.COLLECTION,
        resource_id=collection.id,
        permission=PermissionLevel.MANAGER,
    )
    resp = await client.get(
        f"{api}/documents/{document_id}/review", headers=token_headers(manager.id, org.id)
    )
    assert resp.status_code == 200, resp.text
    audience = resp.json()["audience"]
    assert audience["entries"], audience
    seen = {e["user_id"] for e in audience["entries"]}
    assert str(owner.id) in seen
    assert str(editor.id) in seen
    editor_entry = next(e for e in audience["entries"] if e["user_id"] == str(editor.id))
    assert editor_entry["permission"] == "editor"
    assert editor_entry["via"] == "grant"


async def test_review_audience_reflects_org_visibility_override_in_private_collection(
    client, db_session, token_headers, api, ingest_now
) -> None:
    """A document uploaded with visibility='org' into a PRIVATE collection becomes
    org-readable on approval, so its audience must include every active member (the owner
    plus the three members) and the review must surface the document's own visibility
    override."""
    org, owner, _ = await factories.create_org_with_owner(db_session)
    collection = await factories.create_collection(
        db_session, org=org, owner=owner, visibility=Visibility.PRIVATE
    )
    members = [
        (await factories.add_member(db_session, org=org, role=OrgRole.VIEWER))[0] for _ in range(3)
    ]
    headers = token_headers(owner.id, org.id)

    document_id = await _upload_and_ingest(
        client,
        api,
        headers,
        ingest_now,
        collection,
        title="Gateway runbook",
        content=_AWS_SECRET_TEXT,
        visibility="org",
    )
    assert (await _detail(client, api, headers, document_id))["status"] == "quarantined"

    resp = await client.get(f"{api}/documents/{document_id}/review", headers=headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["document"]["visibility"] == "org"

    audience = body["audience"]
    assert audience["total_users"] == 1 + len(members)
    seen = {e["user_id"] for e in audience["entries"]}
    for member in members:
        assert str(member.id) in seen
        entry = next(e for e in audience["entries"] if e["user_id"] == str(member.id))
        assert entry["permission"] == "viewer"
        assert entry["via"] == "visibility:org (document)"


async def test_review_audience_includes_document_level_grant(
    client, db_session, token_headers, api, ingest_now
) -> None:
    """A grant on the document itself (not the collection) must appear in the audience,
    even inside a PRIVATE collection where it is the member's only path in."""
    org, owner, _ = await factories.create_org_with_owner(db_session)
    collection = await factories.create_collection(
        db_session, org=org, owner=owner, visibility=Visibility.PRIVATE
    )
    headers = token_headers(owner.id, org.id)
    document_id = await _upload_and_ingest(
        client,
        api,
        headers,
        ingest_now,
        collection,
        title="Gateway runbook",
        content=_AWS_SECRET_TEXT,
    )
    assert (await _detail(client, api, headers, document_id))["status"] == "quarantined"

    grantee, _ = await factories.add_member(db_session, org=org, role=OrgRole.VIEWER)
    await factories.grant_user(
        db_session,
        org=org,
        user=grantee,
        resource_type=ResourceType.DOCUMENT,
        resource_id=uuid.UUID(document_id),
        permission=PermissionLevel.VIEWER,
    )

    resp = await client.get(f"{api}/documents/{document_id}/review", headers=headers)
    assert resp.status_code == 200, resp.text
    entries = resp.json()["audience"]["entries"]
    grantee_entry = next((e for e in entries if e["user_id"] == str(grantee.id)), None)
    assert grantee_entry is not None, entries
    assert grantee_entry["permission"] == "viewer"
    assert grantee_entry["via"] == "document-grant"


async def test_reprocess_on_quarantined_document_is_conflict(
    client, db_session, token_headers, api, ingest_now
) -> None:
    """A quarantined document must leave that state only through review (approve/discard),
    never a plain reprocess that would flip it to PENDING and re-expose leftover chunks.

    After the refusal it is still quarantined and untouched.
    """
    org, owner, collection = await _org_with_collection(db_session)
    headers = token_headers(owner.id, org.id)
    document_id = await _quarantined_document(client, api, headers, ingest_now, collection)

    resp = await client.post(f"{api}/documents/{document_id}/reprocess", headers=headers)
    assert resp.status_code == 409, resp.text
    assert "review" in resp.json()["detail"].lower()
    assert (await _detail(client, api, headers, document_id))["status"] == "quarantined"


async def test_approve_reindexes_and_writes_audit_trail(
    client, db_session, token_headers, api, ingest_now
) -> None:
    org, owner, collection = await _org_with_collection(db_session)
    owner_headers = token_headers(owner.id, org.id)
    document_id = await _quarantined_document(client, api, owner_headers, ingest_now, collection)

    editor, _ = await factories.add_member(db_session, org=org, role=OrgRole.VIEWER)
    await factories.grant_user(
        db_session,
        org=org,
        user=editor,
        resource_type=ResourceType.COLLECTION,
        resource_id=collection.id,
        permission=PermissionLevel.EDITOR,
    )

    approved = await client.post(
        f"{api}/documents/{document_id}/approve", headers=token_headers(editor.id, org.id)
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["status"] == "pending"

    await ingest_now(document_id)
    detail = await _detail(client, api, owner_headers, document_id)
    assert detail["status"] == "indexed"
    assert detail["chunk_count"] >= 1

    hits = await _search_doc_ids(client, api, owner_headers, _AWS_QUERY)
    assert document_id in hits

    assert await _audit_rows(db_session, org.id, "document.quarantined", document_id) >= 1
    assert await _audit_rows(db_session, org.id, "document.quarantine_approved", document_id) >= 1


async def test_approve_requires_editor_and_quarantined_status(
    client, db_session, token_headers, api, ingest_now
) -> None:
    org, owner, collection = await _org_with_collection(db_session)
    owner_headers = token_headers(owner.id, org.id)
    document_id = await _quarantined_document(client, api, owner_headers, ingest_now, collection)

    viewer, _ = await factories.add_member(db_session, org=org, role=OrgRole.VIEWER)
    denied = await client.post(
        f"{api}/documents/{document_id}/approve", headers=token_headers(viewer.id, org.id)
    )
    assert denied.status_code == 403, denied.text

    clean_id = await _upload_and_ingest(
        client, api, owner_headers, ingest_now, collection, title="Primer", content=_CLEAN_TEXT
    )
    conflict = await client.post(f"{api}/documents/{clean_id}/approve", headers=owner_headers)
    assert conflict.status_code == 409, conflict.text


async def test_approval_survives_reprocess_of_unchanged_content(
    client, db_session, token_headers, api, ingest_now
) -> None:
    """Approval is keyed to the content checksum: reprocessing the identical content
    must index normally instead of bouncing back into quarantine."""
    org, owner, collection = await _org_with_collection(db_session)
    headers = token_headers(owner.id, org.id)
    document_id = await _quarantined_document(client, api, headers, ingest_now, collection)

    approved = await client.post(f"{api}/documents/{document_id}/approve", headers=headers)
    assert approved.status_code == 200, approved.text
    await ingest_now(document_id)
    assert (await _detail(client, api, headers, document_id))["status"] == "indexed"

    reprocessed = await client.post(f"{api}/documents/{document_id}/reprocess", headers=headers)
    assert reprocessed.status_code == 200, reprocessed.text
    assert reprocessed.json()["status"] == "pending"
    await ingest_now(document_id)

    detail = await _detail(client, api, headers, document_id)
    assert detail["status"] == "indexed"
    assert detail["chunk_count"] >= 1
    hits = await _search_doc_ids(client, api, headers, _AWS_QUERY)
    assert document_id in hits


async def test_discard_deletes_quarantined_document(
    client, db_session, token_headers, api, ingest_now
) -> None:
    org, owner, collection = await _org_with_collection(db_session)
    headers = token_headers(owner.id, org.id)
    document_id = await _quarantined_document(client, api, headers, ingest_now, collection)

    deleted = await client.delete(f"{api}/documents/{document_id}", headers=headers)
    assert deleted.status_code == 200, deleted.text
    gone = await client.get(f"{api}/documents/{document_id}", headers=headers)
    assert gone.status_code == 404


async def test_stale_chunks_of_quarantined_document_are_never_retrievable(
    client, db_session, token_headers, api
) -> None:
    """Chunks left over from a previously indexed version must vanish from retrieval
    the moment the document is quarantined (the search queries gate on INDEXED).

    The first search is the positive control: while INDEXED the document is retrievable.
    """
    org, owner, collection = await _org_with_collection(db_session)
    headers = token_headers(owner.id, org.id)
    document = await factories.create_document(
        db_session,
        org=org,
        collection=collection,
        title="Billing migration",
        content=_MIGRATION_TEXT,
        created_by=owner,
    )

    before = await _search_doc_ids(client, api, headers, _MIGRATION_QUERY)
    assert str(document.id) in before

    document.status = DocumentStatus.QUARANTINED
    await db_session.commit()

    after = await _search_doc_ids(client, api, headers, _MIGRATION_QUERY)
    assert str(document.id) not in after


async def test_clean_content_indexes_without_scan_artifacts(
    client, db_session, token_headers, api, ingest_now
) -> None:
    org, owner, collection = await _org_with_collection(db_session)
    headers = token_headers(owner.id, org.id)

    document_id = await _upload_and_ingest(
        client, api, headers, ingest_now, collection, title="RAG primer", content=_CLEAN_TEXT
    )

    detail_resp = await client.get(f"{api}/documents/{document_id}", headers=headers)
    assert detail_resp.status_code == 200, detail_resp.text
    assert detail_resp.json()["status"] == "indexed"
    assert detail_resp.json()["chunk_count"] >= 1
    assert "secret_scan" not in detail_resp.text

    review = await client.get(f"{api}/documents/{document_id}/review", headers=headers)
    assert review.status_code == 409, review.text

    row = await db_session.get(Document, uuid.UUID(document_id))
    assert "secret_scan" not in (row.meta or {})


async def test_mcp_add_knowledge_quarantines_then_rest_approve_indexes(
    client, db_session, token_headers, api, ingest_now
) -> None:
    """An MCP write is scanned synchronously and quarantined, then approved over REST.

    No key material leaks into the tool result, not even redacted samples, and the
    persisted blob is what makes the quarantined MCP document approvable via REST.
    """
    org, owner, collection = await _org_with_collection(db_session)
    owner_headers = token_headers(owner.id, org.id)
    _key, secret = await factories.create_api_key(
        db_session, org=org, scopes=["search", "ingest"], acts_as_user=owner
    )

    called = await _rpc(
        client,
        method="tools/call",
        params={
            "name": "add_knowledge",
            "arguments": {
                "collection": str(collection.id),
                "title": "VPN bootstrap notes",
                "content": _PRIVATE_KEY_TEXT,
            },
        },
        headers=factories.api_key_headers(secret),
    )
    result = called["result"]
    assert result["isError"] is False, result
    sc = result["structuredContent"]
    assert sc["status"] == "quarantined"
    assert sc["created"] is True
    assert sc["indexed"] is False
    assert sc["chunk_count"] == 0
    assert sc["collection_id"] == str(collection.id)
    assert "quarantined" in sc["message"]
    private_key = next(f for f in sc["findings"] if f["detector"] == "private-key")
    assert private_key["severity"] == "high"
    for finding in sc["findings"]:
        assert set(finding) == {"detector", "label", "severity", "occurrences"}
    assert _PRIVATE_KEY_MARKER not in json.dumps(called)

    document_id = sc["id"]
    assert await _chunk_rows(db_session, document_id) == 0
    assert await _audit_rows(db_session, org.id, "document.quarantined", document_id) >= 1

    approved = await client.post(f"{api}/documents/{document_id}/approve", headers=owner_headers)
    assert approved.status_code == 200, approved.text
    assert approved.json()["status"] == "pending"
    await ingest_now(document_id)
    detail = await _detail(client, api, owner_headers, document_id)
    assert detail["status"] == "indexed"
    assert detail["chunk_count"] >= 1


async def test_mcp_update_knowledge_rejects_flagged_content_and_leaves_document_unchanged(
    client, db_session, token_headers, api
) -> None:
    """A flagged MCP update is rejected outright, leaving the document exactly as it was.

    It stays indexed with the same chunks, its old content stays searchable, and the
    rejected token appears nowhere in its chunks.
    """
    org, owner, collection = await _org_with_collection(db_session)
    owner_headers = token_headers(owner.id, org.id)
    _key, secret = await factories.create_api_key(
        db_session, org=org, scopes=["search", "ingest"], acts_as_user=owner
    )
    key_headers = factories.api_key_headers(secret)

    added = await _rpc(
        client,
        method="tools/call",
        params={
            "name": "add_knowledge",
            "arguments": {
                "collection": str(collection.id),
                "title": "Onboarding",
                "content": _CLEAN_MCP_TEXT,
            },
        },
        headers=key_headers,
    )
    assert added["result"]["isError"] is False, added["result"]
    document_id = added["result"]["structuredContent"]["id"]

    before = await _detail(client, api, owner_headers, document_id)
    assert before["status"] == "indexed"
    assert before["chunk_count"] >= 1

    updated = await _rpc(
        client,
        method="tools/call",
        params={
            "name": "update_knowledge",
            "arguments": {"document_id": document_id, "content": _FLAGGED_UPDATE_TEXT},
        },
        headers=key_headers,
    )
    result = updated["result"]
    assert result["isError"] is True, result
    message = " ".join(block.get("text", "") for block in result.get("content", [])).lower()
    assert "secret" in message, result
    assert _GITHUB_TOKEN not in json.dumps(updated)

    after = await _detail(client, api, owner_headers, document_id)
    assert after["status"] == "indexed"
    assert after["chunk_count"] == before["chunk_count"]

    chunks = await client.get(f"{api}/documents/{document_id}/chunks", headers=owner_headers)
    assert chunks.status_code == 200, chunks.text
    joined = " ".join(c["content"] for c in chunks.json())
    assert _GITHUB_TOKEN not in joined
    assert "onboarding" in joined.lower()

    hits = await _search_doc_ids(client, api, owner_headers, _CLEAN_MCP_QUERY)
    assert document_id in hits
