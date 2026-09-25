"""Frozen repository investigations; reference reports stay outside agent workspaces.

These are authored maintenance fixtures, not samples from deployed services.
The second case is reserved for validation after the first development comparison.
"""

from __future__ import annotations

import hashlib
import json
from textwrap import dedent


def source(text):
    return dedent(text).strip() + "\n"


def document(value):
    return json.dumps(value, ensure_ascii=False, indent=2) + "\n"


def report_part(actual, expected, affected_ids, evidence, cause):
    return {
        "actual": actual,
        "expected": expected,
        "affected_ids": affected_ids,
        "evidence": evidence,
        "cause": cause,
    }


def make_case(case_id, files, questions, reference):
    reference = {"case_id": case_id, "subsystems": reference}
    public_schema = {
        name: {key: value for key, value in item["actual"].items()}
        for name, item in reference["subsystems"].items()
    }
    # Field names/types are public; concrete answers only appear in the hidden grader.
    def shape(value):
        if isinstance(value, dict):
            return {
                key: "object mapping account/customer IDs to integer cents"
                if key.startswith("net_cents_by_")
                else shape(item)
                for key, item in value.items()
            }
        if isinstance(value, list):
            return "array of record IDs (order does not matter)"
        if isinstance(value, int):
            return "integer"
        return "string"

    schema = shape(public_schema)
    prompt = source(
        f"""
        Investigate this repository and write report.json for maintenance review.
        This is a read-only code/contract audit: do not fix or modify existing files.
        Review every subsystem below against its published contract and supplied
        fixture. Report what the current implementation actually does, what the
        contract requires for the same fixture, and the concrete records affected
        by each discrepancy. The smoke tests document supported normal behavior;
        passing them does not establish compliance with the whole contract.

        {questions}

        The report must be a JSON object with case_id={case_id!r} and subsystems.
        Each subsystem has actual, expected, affected_ids, evidence, and cause.
        actual and expected share the following per-subsystem fields/types:
        {json.dumps(schema, ensure_ascii=False)}
        affected_ids contains the input records whose treatment or numeric result
        differs because of the defect; it must not include unrelated records.
        evidence is an array of existing repository-relative file paths (without
        line suffixes), including the implementation, contract, and fixture that
        support this conclusion. cause is one concise explanatory sentence.
        Use integer cents for money, exact fixture identifiers, and JSON arrays
        for ID sets; array order is immaterial. The report should stand alone.
        You may inspect and run the code, but only report.json may change.
        """
    )
    hashes = {
        path: hashlib.sha256(content.encode()).hexdigest() for path, content in files.items()
    }
    grader = source(
        f"""
        import hashlib
        import json
        from pathlib import Path

        EXPECTED = {reference!r}
        HASHES = {hashes!r}

        def normalized(value):
            if isinstance(value, list):
                return sorted(normalized(item) for item in value)
            if isinstance(value, dict):
                return {{key: normalized(item) for key, item in value.items()}}
            return value

        def same_types(actual, expected):
            assert type(actual) is type(expected)
            if isinstance(expected, dict):
                assert set(actual) == set(expected)
                for key in expected:
                    same_types(actual[key], expected[key])
            elif isinstance(expected, list):
                assert all(isinstance(item, str) for item in actual)

        def check_subsystem(name):
            report = json.loads(Path('report.json').read_text())
            assert report['case_id'] == EXPECTED['case_id']
            assert set(report['subsystems']) == set(EXPECTED['subsystems'])
            actual = report['subsystems'][name]
            expected = EXPECTED['subsystems'][name]
            for field in ('actual', 'expected', 'affected_ids'):
                same_types(actual[field], expected[field])
                assert normalized(actual[field]) == normalized(expected[field]), (name, field)
            assert isinstance(actual['cause'], str) and actual['cause'].strip()
            assert isinstance(actual['evidence'], list)
            assert set(expected['evidence']) <= set(actual['evidence'])
            assert all(path in HASHES for path in actual['evidence'])

        def test_source_preserved():
            for path, digest in HASHES.items():
                assert hashlib.sha256(Path(path).read_bytes()).hexdigest() == digest, path
        """
    )
    for name in reference["subsystems"]:
        grader += f"\ndef test_{name}_conclusions():\n    check_subsystem({name!r})\n"
    return {
        "id": case_id,
        "files": files,
        "prompt": prompt,
        "grader": grader,
        "reference_report": reference,
    }


def retention_case():
    files = {
        "README.md": source(
            """
            # Erasure service dry-run review

            Support received a deletion-compliance escalation from tenant north.
            Accounts, object storage, and search publish independent deletion plans.
            The orchestrator applies those plans; it has no additional eligibility
            filters. Review the plans before anyone executes the production jobs.

            Each services/<name>.py module exports plan(batch). Inputs are frozen
            in fixtures/<name>.json. The policies under contracts/ are the approved
            product behavior. All timestamps include an explicit UTC offset.
            IDs and version IDs are unique within a fixture. Plan output order is
            not part of the API contract. Smoke tests cover ordinary successful
            requests and can be run with python -m pytest -q tests.

            Accounts decide hard-deletion eligibility and revoke active sessions.
            Object storage uses per-version retention and legal-hold metadata.
            Search removes documents from indexes that still serve traffic.
            A legal hold does not permit continued login or public search exposure.
            Subsystems do not call each other, and each fixture is self-contained.
            """
        ),
        "contracts/accounts.md": source(
            """
            # Accounts: approved erasure contract

            Input contains tenant, now, grace_hours, users, sessions, and requests.
            A request names an existing user in the same tenant. Duplicate request
            IDs are rejected by the API before this batch job is called.

            Revoke every active session of a requested user immediately, including
            users under legal hold and requests still in the cooling-off period.
            Inactive sessions are already revoked and are not new revocations.
            Sessions and users from another tenant are never touched.

            Hard-delete the account once elapsed time since request.requested_at
            reaches grace_hours. The boundary is inclusive. This gives customers
            time to undo an accidental deletion request. Account creation time is
            unrelated to the cooling-off period. A legal hold prevents hard
            deletion regardless of age, until a separate release is received.

            The plan returns deleted_user_ids, retained_user_ids, and
            revoked_session_ids. The first two lists partition requested users
            that exist in this tenant. Retained users may still lose sessions.
            Nonrequested users do not belong in either list. The job is a dry run:
            it must not mutate the fixture or persist changes.
            """
        ),
        "services/accounts.py": source(
            '''
            """Create a deterministic account deletion plan for an erasure batch."""

            from datetime import datetime


            def timestamp(value):
                result = datetime.fromisoformat(value)
                if result.utcoffset() is None:
                    raise ValueError("timezone required")
                return result


            def requested_users(batch):
                tenant = batch["tenant"]
                users = {
                    row["id"]: row
                    for row in batch["users"]
                    if row["tenant"] == tenant
                }
                selected = {}
                for request in batch["requests"]:
                    if request["tenant"] != tenant:
                        continue
                    user_id = request["user_id"]
                    if user_id in users:
                        selected[user_id] = (users[user_id], request)
                return selected


            def eligible_for_delete(user, request, now, grace_hours):
                if user.get("legal_hold", False):
                    return False
                age = now - timestamp(user["created_at"])
                return age.total_seconds() >= grace_hours * 3600


            def plan(batch):
                now = timestamp(batch["now"])
                grace_hours = int(batch["grace_hours"])
                if grace_hours < 0:
                    raise ValueError("negative grace period")
                selected = requested_users(batch)
                deleted = []
                retained = []
                for user_id, (user, request) in selected.items():
                    timestamp(request["requested_at"])
                    if eligible_for_delete(user, request, now, grace_hours):
                        deleted.append(user_id)
                    else:
                        retained.append(user_id)
                revoked = [
                    session["id"]
                    for session in batch["sessions"]
                    if session["tenant"] == batch["tenant"]
                    and session["user_id"] in selected
                    and session["active"]
                ]
                return {
                    "deleted_user_ids": sorted(deleted),
                    "retained_user_ids": sorted(retained),
                    "revoked_session_ids": sorted(revoked),
                }
            '''
        ),
        "fixtures/accounts.json": document(
            {
                "tenant": "north",
                "now": "2026-09-25T12:00:00+00:00",
                "grace_hours": 72,
                "users": [
                    {"id": "alice", "tenant": "north", "created_at": "2025-01-01T00:00:00+00:00", "legal_hold": False},
                    {"id": "bob", "tenant": "north", "created_at": "2025-02-01T00:00:00+00:00", "legal_hold": False},
                    {"id": "carol", "tenant": "north", "created_at": "2025-03-01T00:00:00+00:00", "legal_hold": True},
                    {"id": "dan", "tenant": "north", "created_at": "2025-04-01T00:00:00+00:00", "legal_hold": False},
                    {"id": "eve", "tenant": "north", "created_at": "2026-09-23T12:00:00+00:00", "legal_hold": False},
                    {"id": "zoe", "tenant": "north", "created_at": "2025-01-01T00:00:00+00:00", "legal_hold": False},
                    {"id": "alice-south", "tenant": "south", "created_at": "2025-01-01T00:00:00+00:00", "legal_hold": False},
                ],
                "requests": [
                    {"user_id": "alice", "tenant": "north", "requested_at": "2026-09-24T12:00:00+00:00"},
                    {"user_id": "bob", "tenant": "north", "requested_at": "2026-09-20T12:00:00+00:00"},
                    {"user_id": "carol", "tenant": "north", "requested_at": "2026-09-20T12:00:00+00:00"},
                    {"user_id": "dan", "tenant": "north", "requested_at": "2026-09-22T12:00:00+00:00"},
                    {"user_id": "eve", "tenant": "north", "requested_at": "2026-09-23T12:00:00+00:00"},
                    {"user_id": "alice-south", "tenant": "south", "requested_at": "2026-09-20T12:00:00+00:00"},
                ],
                "sessions": [
                    {"id": "s-alice", "tenant": "north", "user_id": "alice", "active": True},
                    {"id": "s-alice-old", "tenant": "north", "user_id": "alice", "active": False},
                    {"id": "s-bob", "tenant": "north", "user_id": "bob", "active": True},
                    {"id": "s-carol", "tenant": "north", "user_id": "carol", "active": True},
                    {"id": "s-dan", "tenant": "north", "user_id": "dan", "active": True},
                    {"id": "s-eve", "tenant": "north", "user_id": "eve", "active": True},
                    {"id": "s-zoe", "tenant": "north", "user_id": "zoe", "active": True},
                    {"id": "s-south", "tenant": "south", "user_id": "alice-south", "active": True},
                ],
            }
        ),
        "contracts/objects.md": source(
            """
            # Objects: erasure and minimum retention

            Version IDs identify physical stored versions, not logical keys.
            Erasure applies to every stored version owned by a requested user
            in the target tenant. Overwritten/noncurrent versions still contain
            personal data and must be considered independently of the current one.

            A version is eligible when at least minimum_age_days have elapsed
            since uploaded_at and now is at or after retain_until, if supplied.
            Boundaries are inclusive. legal_hold excludes that physical version
            until the hold is released, even if ordinary retention has expired.
            Retention is evaluated per version; it must not be inherited from a
            newer or older version of the same logical key.

            Requests are already authorized by the API. Nonrequested owners and
            other tenants are outside this plan. The plan must return
            deleted_version_ids and retained_version_ids, partitioning all
            versions belonging to requested owners in the target tenant.
            No storage mutation occurs until an operator approves the dry run.
            """
        ),
        "services/objects.py": source(
            '''
            """Plan physical object-version removal without touching storage."""

            from datetime import datetime, timedelta


            def timestamp(value):
                result = datetime.fromisoformat(value)
                if result.utcoffset() is None:
                    raise ValueError("timezone required")
                return result


            def in_scope(version, tenant, owners):
                return version["tenant"] == tenant and version["owner"] in owners


            def retention_expired(version, now, minimum_age):
                if version.get("legal_hold", False):
                    return False
                if now < timestamp(version["uploaded_at"]) + minimum_age:
                    return False
                retain_until = version.get("retain_until")
                if retain_until and now < timestamp(retain_until):
                    return False
                return True


            def candidates(versions):
                for version in versions:
                    if version["is_current"]:
                        yield version


            def plan(batch):
                now = timestamp(batch["now"])
                days = int(batch["minimum_age_days"])
                if days < 0:
                    raise ValueError("negative minimum age")
                owners = set(batch["requested_owners"])
                scoped = [
                    version
                    for version in batch["versions"]
                    if in_scope(version, batch["tenant"], owners)
                ]
                deleted = {
                    version["version_id"]
                    for version in candidates(scoped)
                    if retention_expired(version, now, timedelta(days=days))
                }
                retained = {
                    version["version_id"] for version in scoped
                } - deleted
                return {
                    "deleted_version_ids": sorted(deleted),
                    "retained_version_ids": sorted(retained),
                }
            '''
        ),
        "fixtures/objects.json": document(
            {
                "tenant": "north",
                "now": "2026-09-25T12:00:00+00:00",
                "minimum_age_days": 30,
                "requested_owners": ["alice", "bob", "carol"],
                "versions": [
                    {"version_id": "report-v1", "key": "exports/report.csv", "tenant": "north", "owner": "bob", "uploaded_at": "2026-08-01T12:00:00+00:00", "is_current": False, "legal_hold": False, "retain_until": None},
                    {"version_id": "report-v2", "key": "exports/report.csv", "tenant": "north", "owner": "bob", "uploaded_at": "2026-08-02T12:00:00+00:00", "is_current": False, "legal_hold": False, "retain_until": None},
                    {"version_id": "report-v3", "key": "exports/report.csv", "tenant": "north", "owner": "bob", "uploaded_at": "2026-08-03T12:00:00+00:00", "is_current": True, "legal_hold": False, "retain_until": None},
                    {"version_id": "photo-v1", "key": "photos/profile.jpg", "tenant": "north", "owner": "alice", "uploaded_at": "2026-09-20T12:00:00+00:00", "is_current": True, "legal_hold": False, "retain_until": None},
                    {"version_id": "held-v1", "key": "legal/consent.pdf", "tenant": "north", "owner": "carol", "uploaded_at": "2026-07-01T12:00:00+00:00", "is_current": True, "legal_hold": True, "retain_until": None},
                    {"version_id": "retained-v1", "key": "tax/receipt.pdf", "tenant": "north", "owner": "bob", "uploaded_at": "2026-07-01T12:00:00+00:00", "is_current": True, "legal_hold": False, "retain_until": "2026-10-01T00:00:00+00:00"},
                    {"version_id": "south-v1", "key": "exports/report.csv", "tenant": "south", "owner": "bob", "uploaded_at": "2026-07-01T12:00:00+00:00", "is_current": True, "legal_hold": False, "retain_until": None},
                    {"version_id": "zoe-v1", "key": "photos/profile.jpg", "tenant": "north", "owner": "zoe", "uploaded_at": "2026-07-01T12:00:00+00:00", "is_current": True, "legal_hold": False, "retain_until": None},
                ],
            }
        ),
        "contracts/search.md": source(
            """
            # Search: serving-index erasure

            Remove documents authored by every requested subject in the target
            tenant from every physical index referenced by serving_aliases.
            Public and staff aliases both serve user queries and have identical
            erasure obligations. Multiple aliases may point to the same index;
            deduplicate document IDs in the returned plan.

            retired_indexes are retained offline for disaster recovery and are
            excluded from this online erasure job. Recovery separately reapplies
            the erasure journal before an index is exposed to traffic. A retired
            index must not be counted as currently searchable merely because its
            documents appear in the inventory fixture.

            Search erasure is immediate. Account cooling-off windows and legal
            holds do not permit continued exposure in public or staff search.
            Other tenants and documents by nonrequested authors remain intact.

            Return removed_document_ids for the current plan and
            still_searchable_document_ids for requested authors' documents that
            remain in a serving index after that plan. There is no hidden second
            sweep: the orchestrator applies exactly the IDs returned here.
            """
        ),
        "services/search.py": source(
            '''
            """Resolve search aliases and compute an online erasure dry run."""


            def serving_indexes(config):
                aliases = config["serving_aliases"]
                if not isinstance(aliases, dict) or "public" not in aliases:
                    raise ValueError("public serving alias required")
                return set(aliases.values())


            def online_documents(batch):
                serving = serving_indexes(batch["config"])
                tenant = batch["tenant"]
                return [
                    doc for doc in batch["documents"]
                    if doc["tenant"] == tenant and doc["index"] in serving
                ]


            def deletion_candidates(batch):
                index = batch["config"]["serving_aliases"]["public"]
                authors = set(batch["requested_subjects"])
                for document in batch["documents"]:
                    if document["tenant"] != batch["tenant"]:
                        continue
                    if document["index"] != index:
                        continue
                    if document["author_id"] in authors:
                        yield document


            def plan(batch):
                online = online_documents(batch)
                authors = set(batch["requested_subjects"])
                removed = {
                    document["id"] for document in deletion_candidates(batch)
                }
                remaining = {
                    document["id"]
                    for document in online
                    if document["author_id"] in authors
                    and document["id"] not in removed
                }
                return {
                    "removed_document_ids": sorted(removed),
                    "still_searchable_document_ids": sorted(remaining),
                }
            '''
        ),
        "fixtures/search.json": document(
            {
                "tenant": "north",
                "requested_subjects": ["alice", "bob", "carol", "dan", "eve"],
                "config": {
                    "serving_aliases": {"public": "posts_v4", "staff": "staff_v2", "moderation": "staff_v2"},
                    "retired_indexes": ["posts_v3"],
                },
                "documents": [
                    {"id": "public-alice", "tenant": "north", "index": "posts_v4", "author_id": "alice"},
                    {"id": "public-carol", "tenant": "north", "index": "posts_v4", "author_id": "carol"},
                    {"id": "public-zoe", "tenant": "north", "index": "posts_v4", "author_id": "zoe"},
                    {"id": "staff-bob", "tenant": "north", "index": "staff_v2", "author_id": "bob"},
                    {"id": "staff-eve", "tenant": "north", "index": "staff_v2", "author_id": "eve"},
                    {"id": "staff-zoe", "tenant": "north", "index": "staff_v2", "author_id": "zoe"},
                    {"id": "retired-dan", "tenant": "north", "index": "posts_v3", "author_id": "dan"},
                    {"id": "south-alice", "tenant": "south", "index": "posts_v4", "author_id": "alice"},
                ],
            }
        ),
        "tests/test_smoke.py": source(
            """
            from services import accounts, objects, search


            def test_empty_erasure_batch():
                assert accounts.plan({
                    'tenant': 'north', 'now': '2026-09-25T12:00:00+00:00',
                    'grace_hours': 72, 'users': [], 'sessions': [], 'requests': [],
                }) == {'deleted_user_ids': [], 'retained_user_ids': [], 'revoked_session_ids': []}


            def test_held_object_stays():
                version = {'version_id': 'v', 'tenant': 'north', 'owner': 'u',
                           'is_current': True, 'legal_hold': True,
                           'uploaded_at': '2025-01-01T00:00:00+00:00'}
                assert objects.plan({
                    'tenant': 'north', 'now': '2026-09-25T12:00:00+00:00',
                    'minimum_age_days': 30, 'requested_owners': ['u'], 'versions': [version],
                }) == {'deleted_version_ids': [], 'retained_version_ids': ['v']}


            def test_public_document_removed():
                assert search.plan({
                    'tenant': 'north', 'requested_subjects': ['u'],
                    'config': {'serving_aliases': {'public': 'p'}},
                    'documents': [{'id': 'd', 'tenant': 'north', 'author_id': 'u', 'index': 'p'}],
                }) == {'removed_document_ids': ['d'], 'still_searchable_document_ids': []}
            """
        ),
    }
    sessions = ["s-alice", "s-bob", "s-carol", "s-dan", "s-eve"]
    reference = {
        "accounts": report_part(
            {"deleted_user_ids": ["alice", "bob", "dan"], "retained_user_ids": ["carol", "eve"], "revoked_session_ids": sessions},
            {"deleted_user_ids": ["bob", "dan"], "retained_user_ids": ["alice", "carol", "eve"], "revoked_session_ids": sessions},
            ["alice"],
            ["services/accounts.py", "contracts/accounts.md", "fixtures/accounts.json"],
            "The cooling-off interval starts at account creation instead of the erasure request, deleting Alice early.",
        ),
        "objects": report_part(
            {"deleted_version_ids": ["report-v3"], "retained_version_ids": ["held-v1", "photo-v1", "report-v1", "report-v2", "retained-v1"]},
            {"deleted_version_ids": ["report-v1", "report-v2", "report-v3"], "retained_version_ids": ["held-v1", "photo-v1", "retained-v1"]},
            ["report-v1", "report-v2"],
            ["services/objects.py", "contracts/objects.md", "fixtures/objects.json"],
            "Candidate filtering ignores noncurrent versions even when their independent retention has expired.",
        ),
        "search": report_part(
            {"removed_document_ids": ["public-alice", "public-carol"], "still_searchable_document_ids": ["staff-bob", "staff-eve"]},
            {"removed_document_ids": ["public-alice", "public-carol", "staff-bob", "staff-eve"], "still_searchable_document_ids": []},
            ["staff-bob", "staff-eve"],
            ["services/search.py", "contracts/search.md", "fixtures/search.json"],
            "Deletion candidates only use the public alias while staff and moderation also serve the affected documents.",
        ),
    }
    return make_case(
        "retention-audit",
        files,
        """1. accounts: Determine deletion, retention, and immediate session revocation for fixtures/accounts.json.
        2. objects: Determine deletion/retention of physical versions in fixtures/objects.json.
        3. search: Determine removed and still-searchable documents in fixtures/search.json.
        affected_ids uses user IDs for accounts, version IDs for objects, and document IDs for search.""",
        reference,
    )


def reconciliation_case():
    files = {
        "README.md": source(
            """
            # Reconciliation import review

            Finance observed discrepancies after importing three providers.
            Independently review the bank statement adapter, subscription-event
            adapter, and marketplace settlement adapter. They feed different
            ledgers: there is no cross-provider balance to calculate.

            adapters/<name>.py exports reconcile(batch), a pure dry-run function.
            fixtures/<name>.json is the captured provider payload plus configured
            account mapping or settlement window. contracts/<name>.md is the
            approved accounting behavior, including rounding and deduplication.
            All reported balances are integer cents in the configured currency.

            Do not repair imports or reformat source as part of this investigation.
            Derive actual and required outputs, then identify the input records
            affected by each discrepancy. The smoke tests are useful but cover
            only ordinary cases. Run them with python -m pytest -q tests if useful.
            Input order follows provider delivery order. Only duplicate IDs or
            refund identities stated in the contracts should be deduplicated.
            """
        ),
        "contracts/bank.md": source(
            """
            # Bank statement adapter contract

            account_map maps provider routing IDs to internal cash-account IDs.
            Reject records with unknown routing IDs, nonpositive or nonfinite
            amounts, invalid direction, or an invalid timestamp. A valid amount
            is a decimal string in the account currency, without locale commas.

            Process records in delivery order. Successful records are idempotent
            by (routing_id, external_ref); a duplicate has no balance effect and
            appears once in duplicate_refs. Rejected records do not consume an
            idempotency key. IDs in output use routing_id:external_ref, including
            rejected rows, so the operator can find the provider records.

            Convert to cents using decimal ROUND_HALF_UP, then apply the sign:
            credits increase cash and debits decrease cash. Fractional cents must
            not silently truncate. For example, a halfway value rounds away from
            zero after the direction sign is applied. Preserve exact decimals;
            do not introduce binary float rounding.

            Return net_cents_by_account for every configured internal account,
            accepted_refs, duplicate_refs, and rejected_refs. Start balances at
            zero for this batch. Do not mutate the input payload.
            """
        ),
        "adapters/bank.py": source(
            '''
            """Normalize bank rows into a batch cash reconciliation."""

            from datetime import datetime
            from decimal import Decimal, InvalidOperation, ROUND_DOWN


            def cents(text, direction):
                amount = Decimal(text)
                if not amount.is_finite() or amount <= 0:
                    raise ValueError("positive finite amount required")
                if direction not in {"credit", "debit"}:
                    raise ValueError("unknown direction")
                magnitude = int((amount * 100).quantize(Decimal("1"), rounding=ROUND_DOWN))
                return magnitude if direction == "credit" else -magnitude


            def validate_time(value):
                stamp = datetime.fromisoformat(value)
                if stamp.utcoffset() is None:
                    raise ValueError("timezone required")


            def reconcile(batch):
                mapping = batch["account_map"]
                totals = {account: 0 for account in mapping.values()}
                seen = set()
                accepted = []
                rejected = set()
                duplicates = set()
                for row in batch["rows"]:
                    key = (row["routing_id"], row["external_ref"])
                    reference = ":".join(key)
                    if key in seen:
                        duplicates.add(reference)
                        continue
                    try:
                        account = mapping[row["routing_id"]]
                        validate_time(row["booked_at"])
                        value = cents(row["amount"], row["direction"])
                    except (KeyError, ValueError, InvalidOperation):
                        rejected.add(reference)
                        continue
                    totals[account] += value
                    accepted.append(reference)
                    seen.add(key)
                return {
                    "net_cents_by_account": totals,
                    "accepted_refs": sorted(accepted),
                    "duplicate_refs": sorted(duplicates),
                    "rejected_refs": sorted(rejected),
                }
            '''
        ),
        "fixtures/bank.json": document(
            {
                "currency": "USD",
                "account_map": {"checking": "cash-main", "savings": "cash-reserve"},
                "rows": [
                    {"routing_id": "checking", "external_ref": "payroll", "direction": "credit", "amount": "100.00", "booked_at": "2026-09-25T09:00:00+00:00"},
                    {"routing_id": "checking", "external_ref": "fee", "direction": "debit", "amount": "10.005", "booked_at": "2026-09-25T09:10:00+00:00"},
                    {"routing_id": "savings", "external_ref": "interest", "direction": "credit", "amount": "3.335", "booked_at": "2026-09-25T09:20:00+00:00"},
                    {"routing_id": "checking", "external_ref": "payroll", "direction": "credit", "amount": "100.00", "booked_at": "2026-09-25T09:00:00+00:00"},
                    {"routing_id": "unknown", "external_ref": "transfer", "direction": "credit", "amount": "20.00", "booked_at": "2026-09-25T10:00:00+00:00"},
                    {"routing_id": "checking", "external_ref": "bad", "direction": "credit", "amount": "NaN", "booked_at": "2026-09-25T10:05:00+00:00"},
                ],
            }
        ),
        "contracts/subscriptions.md": source(
            """
            # Subscription webhook reconciliation contract

            Process the supplied delivery-order stream for the configured currency.
            invoice.paid contributes amount_cents to the named customer balance.
            charge.refunded subtracts amount_cents from that invoice's customer.
            Refund amounts are incremental, not cumulative. Validate positive
            integer cents and reject unsupported event types or currencies.

            A successful event_id is idempotent. The provider may also reissue a
            refund under a different event_id after webhook redelivery. A refund
            is economically unique by (invoice_id, refund_id); apply it once even
            if it arrives in multiple distinct envelopes. That second envelope's
            event_id belongs in duplicate_event_ids and adds no balance movement.
            Invoice-payment events in this fixture have unique invoice IDs unless
            the event_id itself is repeated. Refunds always follow their invoice.

            Reject a refund for an invoice that has not been accepted, or if its
            customer differs from the accepted invoice's customer. Invalid events
            do not consume event or refund idempotency keys. All customer IDs in
            accepted events appear in net_cents_by_customer, including zero totals.

            Return accepted_event_ids, duplicate_event_ids, rejected_event_ids,
            and net_cents_by_customer. Array order is not part of the API. This
            report covers the supplied batch only; no earlier balance is assumed.
            """
        ),
        "adapters/subscriptions.py": source(
            '''
            """Fold subscription webhook deliveries into customer balances."""


            def validate(event, currency, invoices):
                if event["currency"] != currency:
                    raise ValueError("unsupported currency")
                amount = event["amount_cents"]
                if type(amount) is not int or amount <= 0:
                    raise ValueError("positive integer amount required")
                kind = event["type"]
                if kind == "invoice.paid":
                    return amount
                if kind == "charge.refunded":
                    invoice = invoices.get(event["invoice_id"])
                    if invoice is None or invoice != event["customer_id"]:
                        raise ValueError("refund invoice/customer mismatch")
                    if not event.get("refund_id"):
                        raise ValueError("refund identity required")
                    return -amount
                raise ValueError("unsupported event type")


            def reconcile(batch):
                seen_events = set()
                invoices = {}
                totals = {}
                accepted = []
                duplicates = set()
                rejected = set()
                for event in batch["events"]:
                    identity = event["event_id"]
                    if identity in seen_events:
                        duplicates.add(identity)
                        continue
                    try:
                        delta = validate(event, batch["currency"], invoices)
                    except (KeyError, ValueError):
                        rejected.add(identity)
                        continue
                    customer = event["customer_id"]
                    if event["type"] == "invoice.paid":
                        invoices[event["invoice_id"]] = customer
                    totals[customer] = totals.get(customer, 0) + delta
                    seen_events.add(identity)
                    accepted.append(identity)
                return {
                    "net_cents_by_customer": totals,
                    "accepted_event_ids": sorted(accepted),
                    "duplicate_event_ids": sorted(duplicates),
                    "rejected_event_ids": sorted(rejected),
                }
            '''
        ),
        "fixtures/subscriptions.json": document(
            {
                "currency": "USD",
                "events": [
                    {"event_id": "evt-p1", "type": "invoice.paid", "invoice_id": "inv-1", "customer_id": "alex", "currency": "USD", "amount_cents": 10000},
                    {"event_id": "evt-r1", "type": "charge.refunded", "invoice_id": "inv-1", "refund_id": "refund-1", "customer_id": "alex", "currency": "USD", "amount_cents": 2500},
                    {"event_id": "evt-r1-replay", "type": "charge.refunded", "invoice_id": "inv-1", "refund_id": "refund-1", "customer_id": "alex", "currency": "USD", "amount_cents": 2500},
                    {"event_id": "evt-p2", "type": "invoice.paid", "invoice_id": "inv-2", "customer_id": "blair", "currency": "USD", "amount_cents": 4800},
                    {"event_id": "evt-r2", "type": "charge.refunded", "invoice_id": "inv-2", "refund_id": "refund-2", "customer_id": "blair", "currency": "USD", "amount_cents": 4800},
                    {"event_id": "evt-p2", "type": "invoice.paid", "invoice_id": "inv-2", "customer_id": "blair", "currency": "USD", "amount_cents": 4800},
                    {"event_id": "evt-fx", "type": "invoice.paid", "invoice_id": "inv-3", "customer_id": "casey", "currency": "EUR", "amount_cents": 1900},
                ],
            }
        ),
        "contracts/marketplace.md": source(
            """
            # Marketplace daily settlement contract

            A batch reconciles one store, currency, and local business_date.
            settled_at is an offset-bearing ISO timestamp from the provider.
            Convert it into business_timezone before taking its calendar date.
            The configured business zone is authoritative even when the provider
            sends UTC. Day boundaries are local midnight, including DST rules.

            Ignore payouts belonging to other stores. For this store, validate
            currency, integer gross_cents/fee_cents, and timestamp. Malformed
            payouts appear in rejected_payout_ids regardless of date. Successfully
            validated payouts outside the date are not rejected: they belong to a
            different day's reconciliation. Provider payout IDs are unique here.

            net_cents = gross_cents - fee_cents for each selected payout. Refunds
            arrive as negative gross and may carry a negative fee credit; use the
            same formula rather than subtracting the fee's absolute value.

            Return selected_payout_ids, rejected_payout_ids, and total net_cents.
            selected_payout_ids names individual payout lines, not order IDs.
            affected_ids in a review identifies valid payouts put on the wrong
            business day, not every selected payout or an aggregate account ID.
            """
        ),
        "adapters/marketplace.py": source(
            '''
            """Produce a daily marketplace settlement reconciliation."""

            from datetime import date, datetime
            from zoneinfo import ZoneInfo


            def validate(row, currency):
                if row["currency"] != currency:
                    raise ValueError("unsupported currency")
                if type(row["gross_cents"]) is not int:
                    raise ValueError("integer gross required")
                if type(row["fee_cents"]) is not int:
                    raise ValueError("integer fee required")
                timestamp = datetime.fromisoformat(row["settled_at"])
                if timestamp.utcoffset() is None:
                    raise ValueError("timezone required")
                return timestamp


            def reporting_day(timestamp, timezone):
                return timestamp.date()


            def reconcile(batch):
                day = date.fromisoformat(batch["business_date"])
                timezone = ZoneInfo(batch["business_timezone"])
                selected = []
                rejected = []
                net = 0
                for row in batch["payouts"]:
                    if row["store_id"] != batch["store_id"]:
                        continue
                    try:
                        timestamp = validate(row, batch["currency"])
                    except (ValueError, KeyError):
                        rejected.append(row["payout_id"])
                        continue
                    if reporting_day(timestamp, timezone) != day:
                        continue
                    selected.append(row["payout_id"])
                    net += row["gross_cents"] - row["fee_cents"]
                return {
                    "selected_payout_ids": sorted(selected),
                    "rejected_payout_ids": sorted(rejected),
                    "net_cents": net,
                }
            '''
        ),
        "fixtures/marketplace.json": document(
            {
                "store_id": "north-shop",
                "currency": "EUR",
                "business_date": "2026-09-25",
                "business_timezone": "Europe/Berlin",
                "payouts": [
                    {"payout_id": "p-early", "store_id": "north-shop", "currency": "EUR", "settled_at": "2026-09-24T22:30:00+00:00", "gross_cents": 10000, "fee_cents": 1000},
                    {"payout_id": "p-noon", "store_id": "north-shop", "currency": "EUR", "settled_at": "2026-09-25T12:00:00+00:00", "gross_cents": 5000, "fee_cents": 500},
                    {"payout_id": "p-late", "store_id": "north-shop", "currency": "EUR", "settled_at": "2026-09-25T22:30:00+00:00", "gross_cents": 2000, "fee_cents": 200},
                    {"payout_id": "p-refund", "store_id": "north-shop", "currency": "EUR", "settled_at": "2026-09-25T14:00:00+00:00", "gross_cents": -1000, "fee_cents": -100},
                    {"payout_id": "p-other", "store_id": "south-shop", "currency": "EUR", "settled_at": "2026-09-25T12:00:00+00:00", "gross_cents": 80000, "fee_cents": 8000},
                    {"payout_id": "p-invalid", "store_id": "north-shop", "currency": "EUR", "settled_at": "missing", "gross_cents": 100, "fee_cents": 10},
                ],
            }
        ),
        "tests/test_smoke.py": source(
            """
            from adapters import bank, marketplace, subscriptions


            def test_bank_integer_cent_conversion():
                assert bank.cents('12.50', 'credit') == 1250
                assert bank.cents('12.50', 'debit') == -1250


            def test_repeated_subscription_event_is_ignored():
                event = {'event_id': 'e', 'type': 'invoice.paid', 'invoice_id': 'i',
                         'customer_id': 'u', 'currency': 'USD', 'amount_cents': 500}
                result = subscriptions.reconcile({'currency': 'USD', 'events': [event, event]})
                assert result['net_cents_by_customer'] == {'u': 500}
                assert result['duplicate_event_ids'] == ['e']


            def test_marketplace_midday_and_fee_credit():
                row = {'payout_id': 'p', 'store_id': 's', 'currency': 'EUR',
                       'settled_at': '2026-09-25T12:00:00+00:00',
                       'gross_cents': -1000, 'fee_cents': -100}
                assert marketplace.reconcile({
                    'store_id': 's', 'currency': 'EUR', 'business_date': '2026-09-25',
                    'business_timezone': 'Europe/Berlin', 'payouts': [row],
                }) == {'selected_payout_ids': ['p'], 'rejected_payout_ids': [], 'net_cents': -900}
            """
        ),
    }
    bank_common = {
        "accepted_refs": ["checking:fee", "checking:payroll", "savings:interest"],
        "duplicate_refs": ["checking:payroll"],
        "rejected_refs": ["checking:bad", "unknown:transfer"],
    }
    reference = {
        "bank": report_part(
            {"net_cents_by_account": {"cash-main": 9000, "cash-reserve": 333}, **bank_common},
            {"net_cents_by_account": {"cash-main": 8999, "cash-reserve": 334}, **bank_common},
            ["checking:fee", "savings:interest"],
            ["adapters/bank.py", "contracts/bank.md", "fixtures/bank.json"],
            "The adapter truncates fractional cents with ROUND_DOWN instead of the specified ROUND_HALF_UP.",
        ),
        "subscriptions": report_part(
            {"net_cents_by_customer": {"alex": 5000, "blair": 0}, "accepted_event_ids": ["evt-p1", "evt-p2", "evt-r1", "evt-r1-replay", "evt-r2"], "duplicate_event_ids": ["evt-p2"], "rejected_event_ids": ["evt-fx"]},
            {"net_cents_by_customer": {"alex": 7500, "blair": 0}, "accepted_event_ids": ["evt-p1", "evt-p2", "evt-r1", "evt-r2"], "duplicate_event_ids": ["evt-p2", "evt-r1-replay"], "rejected_event_ids": ["evt-fx"]},
            ["evt-r1-replay"],
            ["adapters/subscriptions.py", "contracts/subscriptions.md", "fixtures/subscriptions.json"],
            "Distinct webhook envelopes for the same invoice/refund identity are both applied because deduplication only uses event_id.",
        ),
        "marketplace": report_part(
            {"selected_payout_ids": ["p-late", "p-noon", "p-refund"], "rejected_payout_ids": ["p-invalid"], "net_cents": 5400},
            {"selected_payout_ids": ["p-early", "p-noon", "p-refund"], "rejected_payout_ids": ["p-invalid"], "net_cents": 12600},
            ["p-early", "p-late"],
            ["adapters/marketplace.py", "contracts/marketplace.md", "fixtures/marketplace.json"],
            "The reporting date is taken before conversion to Europe/Berlin, assigning boundary payouts to the wrong local day.",
        ),
    }
    return make_case(
        "reconciliation-audit",
        files,
        """1. bank: Calculate per-account cash movement and acceptance/deduplication results for fixtures/bank.json.
        2. subscriptions: Calculate per-customer movement and webhook classification for fixtures/subscriptions.json.
        3. marketplace: Calculate the configured local day's selected payouts and net settlement for fixtures/marketplace.json.
        affected_ids uses routing_id:external_ref for bank, event_id for subscriptions, and payout_id for marketplace.""",
        reference,
    )


def cases():
    return [retention_case(), reconciliation_case()]
