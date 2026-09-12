from __future__ import annotations

import copy
from types import SimpleNamespace

import pytest

from app.models import Project
from app.models.case import TestCase as _TestCaseModel
from app.models.case import TestSuite as _TestSuiteModel
from scripts import cleanup_removed_viewport_params as cleanup


class _ScalarResult:
    def __init__(self, rows):
        self.rows = rows

    def all(self):
        return self.rows


class _FakeSession:
    def __init__(self, *, cases, suites, projects):
        self.cases = cases
        self.suites = suites
        self.projects = projects
        self.commits = 0
        self.rollbacks = 0
        self._snapshot = copy.deepcopy((cases, suites, projects))

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return False

    async def scalars(self, statement):
        entity = statement.column_descriptions[0].get("entity")
        is_project_filtered = "project_id" in str(statement.whereclause)
        if entity is _TestCaseModel:
            rows = self.cases
            if is_project_filtered:
                rows = [row for row in rows if row.project_id == 7]
        elif entity is _TestSuiteModel:
            rows = self.suites
            if is_project_filtered:
                rows = [row for row in rows if row.project_id == 7]
        elif entity is Project:
            rows = self.projects
            if " IN " in str(statement.whereclause):
                rows = [row for row in rows if row.id == 7]
        else:  # pragma: no cover - catches a changed query shape
            raise AssertionError(f"unexpected query entity: {entity}")
        return _ScalarResult(rows)

    async def commit(self):
        self.commits += 1

    async def rollback(self):
        self.rollbacks += 1
        cases, suites, projects = copy.deepcopy(self._snapshot)
        self.cases[:] = cases
        self.suites[:] = suites
        self.projects[:] = projects


def _session_factory(monkeypatch, session):
    monkeypatch.setattr(cleanup, "SessionLocal", lambda: session)


def _fixture_session():
    project = SimpleNamespace(id=7, test_asset_revision=3)
    case = SimpleNamespace(
        id=1,
        project_id=7,
        flow_nodes=[
            {
                "action": "swipe",
                "params": {
                    "viewport_mode": "parent",
                    "nested": [{"viewport_element_id": "old", "keep": 1}],
                },
                "viewport_mode": "business-field",
            }
        ],
        steps=[{"parameters": {"viewport_element_id": "old", "duration": 1}}],
    )
    suite = SimpleNamespace(
        id=2,
        project_id=7,
        setup_steps=[{"params": {"viewport_mode": "self"}}],
        teardown_steps=[{"name": "keep", "viewport_element_id": "business-field"}],
    )
    return _FakeSession(cases=[case], suites=[suite], projects=[project])


@pytest.mark.asyncio
async def test_dry_run_rolls_back_and_apply_updates_each_revision_once(monkeypatch):
    dry_session = _fixture_session()
    original = copy.deepcopy((dry_session.cases, dry_session.suites))
    _session_factory(monkeypatch, dry_session)

    dry_stats = await cleanup.run()

    assert dry_session.commits == 0
    assert dry_session.rollbacks == 1
    assert (dry_session.cases, dry_session.suites) == original
    assert dry_session.projects[0].test_asset_revision == 3
    assert dry_stats["deleted_fields"] == 4
    assert dry_stats["project_revisions_bumped"] == 1
    assert dry_stats["profile_revisions_bumped"] == 0

    apply_session = _fixture_session()
    _session_factory(monkeypatch, apply_session)
    apply_stats = await cleanup.run(apply=True)

    assert apply_session.commits == 1
    assert apply_session.rollbacks == 0
    assert apply_stats["affected_rows"] == 2
    assert apply_session.projects[0].test_asset_revision == 4
    assert "viewport_mode" not in apply_session.cases[0].flow_nodes[0]["params"]
    assert "viewport_element_id" not in apply_session.cases[0].flow_nodes[0]["params"]["nested"][0]
    assert "viewport_element_id" not in apply_session.cases[0].steps[0]["parameters"]
    assert "viewport_mode" not in apply_session.suites[0].setup_steps[0]["params"]
    assert apply_session.suites[0].teardown_steps[0]["viewport_element_id"] == "business-field"


@pytest.mark.asyncio
async def test_second_public_scan_is_idempotent(monkeypatch):
    session = _fixture_session()
    _session_factory(monkeypatch, session)

    first = await cleanup.run(apply=True, project_id=7)
    assert first["deleted_fields"] == 4

    # A second scan sees the cleaned public nodes and has nothing left to remove.
    second = await cleanup.run(apply=True, project_id=7)
    assert second["deleted_fields"] == 0
    assert second["affected_rows"] == 0


def test_clean_value_only_removes_fields_inside_parameter_objects():
    value = {
        "viewport_mode": "business",
        "params": {
            "viewport_mode": "parent",
            "items": [{"parameters": {"viewport_element_id": 1, "keep": 2}}],
        },
    }

    cleaned, removed = cleanup._clean_value(value)

    assert removed == 2
    assert cleaned["viewport_mode"] == "business"
    assert "viewport_mode" not in cleaned["params"]
    assert cleaned["params"]["items"][0]["parameters"] == {"keep": 2}
    assert value["params"]["viewport_mode"] == "parent"


def test_project_id_is_validated():
    with pytest.raises(ValueError, match="正整数"):
        cleanup._validate_project_id(0)


@pytest.mark.asyncio
async def test_project_filter_does_not_touch_other_project(monkeypatch):
    session = _fixture_session()
    other_project = SimpleNamespace(id=8, test_asset_revision=12)
    other_case = SimpleNamespace(
        id=8,
        project_id=8,
        flow_nodes=[{"params": {"viewport_mode": "parent"}}],
        steps=[],
    )
    other_suite = SimpleNamespace(
        id=8,
        project_id=8,
        setup_steps=[{"params": {"viewport_element_id": "keep"}}],
        teardown_steps=[],
    )
    session.projects.append(other_project)
    session.cases.append(other_case)
    session.suites.append(other_suite)
    _session_factory(monkeypatch, session)

    stats = await cleanup.run(apply=True, project_id=7)

    assert stats["affected_rows"] == 2
    assert "viewport_mode" not in session.cases[0].flow_nodes[0]["params"]
    assert session.cases[1].flow_nodes[0]["params"]["viewport_mode"] == "parent"
    assert session.suites[1].setup_steps[0]["params"]["viewport_element_id"] == "keep"
    assert other_project.test_asset_revision == 12
