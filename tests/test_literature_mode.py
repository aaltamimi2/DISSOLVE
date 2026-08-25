"""/literature is a tool-list gate. Ingest is never offered. Off is the default."""
from __future__ import annotations

import inspect
import io
import sys
from pathlib import Path

import pytest
from rich.console import Console

_ROOT = Path(__file__).resolve().parents[1]
for _path in (str(_ROOT), str(_ROOT / "src")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from agent_tools import (
    LITERATURE_AGENT_TOOLS,
    LITERATURE_CORPUS_TOOLS,
    LITERATURE_INGEST_TOOLS,
    LITERATURE_MODE_SURFACE,
    LITERATURE_NETWORK_TOOLS,
    LITERATURE_SCHOLARLY_TOOLS,
    dispatch,
    literature_agent_mode,
    offered_tool_names,
    tool_schemas,
)
from dissolve import registry, research
from dissolve.cli import (
    CliApp,
    EXPECTED_REGISTRY_NAMES,
    _format_literature_default,
    _parse_literature_slash,
)
from dissolve.contracts import parse_tool_result


def _app(tmp_path, monkeypatch, *, session_id: str = "literature-cli", **kwargs):
    monkeypatch.setenv("META_MUSE_API_KEY", "test-key")
    buf = io.StringIO()
    app = CliApp(
        session_id=session_id,
        store_root=tmp_path,
        console=Console(file=buf, force_terminal=True, width=80, color_system=None),
        **kwargs,
    )
    return app, buf


def _literature_names(session=None) -> set[str]:
    return {item["name"] for item in tool_schemas(session)} & set(LITERATURE_AGENT_TOOLS)


def test_parse_rejects_on_and_unknown():
    assert _parse_literature_slash([]) is None
    assert _parse_literature_slash(["off"]) == {"mode": "off"}
    assert _parse_literature_slash(["corpus"]) == {"mode": "corpus"}
    assert _parse_literature_slash(["scholarly"]) == {"mode": "scholarly"}
    with pytest.raises(ValueError, match="usage: /literature"):
        _parse_literature_slash(["on"])
    with pytest.raises(ValueError, match="usage: /literature"):
        _parse_literature_slash(["network"])
    with pytest.raises(ValueError, match="usage: /literature"):
        _parse_literature_slash(["ingest"])
    assert _format_literature_default(None, origin="built-in") == (
        "literature_mode=off  (built-in)"
    )


def test_off_default_six_literature_tools_absent_from_agent_list():
    names = _literature_names()
    assert names == set()
    assert LITERATURE_AGENT_TOOLS.isdisjoint(offered_tool_names())
    assert "ingest_literature_documents" in registry.BY_NAME
    assert "ingest_literature_graph" in registry.BY_NAME
    assert len(EXPECTED_REGISTRY_NAMES) == 29
    assert len(registry.REGISTRY) == 29


def test_corpus_offers_exactly_two_local_tools_and_network_does_not_fire(monkeypatch, tmp_path):
    def boom(*_args, **_kwargs):
        raise AssertionError("corpus mode must not hit scholarly/patent network")

    monkeypatch.setattr(research, "_search_arxiv", boom)
    monkeypatch.setattr(research, "_search_google_scholar", boom)
    monkeypatch.setattr(research, "_search_wos", boom)
    monkeypatch.setattr(research, "_search_google_patents", boom)
    monkeypatch.setattr(research, "_search_patentsview", boom)
    monkeypatch.setattr(research, "search_scholarly_literature", boom)
    monkeypatch.setattr(research, "search_patent_literature", boom)
    session = {"literature_mode": {"mode": "corpus"}}
    assert _literature_names(session) == set(LITERATURE_CORPUS_TOOLS)
    assert LITERATURE_NETWORK_TOOLS.isdisjoint(_literature_names(session))
    assert LITERATURE_INGEST_TOOLS.isdisjoint(_literature_names(session))
    monkeypatch.setenv("DISSOLVE_RESEARCH_HOME", str(tmp_path))
    payload = parse_tool_result(
        research.inspect_literature_corpus(operation="status"),
    )
    assert payload["data"]["success"] is True


def test_scholarly_is_named_surface_not_registry_minus_ingest():
    assert set(LITERATURE_MODE_SURFACE) == {"off", "corpus", "scholarly"}
    assert "ingest" not in LITERATURE_MODE_SURFACE
    assert LITERATURE_MODE_SURFACE["off"] == frozenset()
    assert LITERATURE_MODE_SURFACE["corpus"] == LITERATURE_CORPUS_TOOLS
    assert LITERATURE_MODE_SURFACE["scholarly"] == LITERATURE_SCHOLARLY_TOOLS
    assert LITERATURE_SCHOLARLY_TOOLS == LITERATURE_CORPUS_TOOLS | LITERATURE_NETWORK_TOOLS
    offered_across_modes = frozenset().union(*LITERATURE_MODE_SURFACE.values())
    assert LITERATURE_INGEST_TOOLS.isdisjoint(offered_across_modes)
    assert literature_agent_mode({"literature_mode": {"mode": "ingest"}}) == "off"


def test_scholarly_offers_four_and_ingest_never():
    session = {"literature_mode": {"mode": "scholarly"}}
    names = _literature_names(session)
    assert names == set(LITERATURE_SCHOLARLY_TOOLS)
    assert len(names) == 4
    for mode in (None, {"literature_mode": {"mode": "off"}}, session, {"literature_mode": {"mode": "corpus"}}):
        assert LITERATURE_INGEST_TOOLS.isdisjoint(_literature_names(mode))
    scholarly = {item["name"]: item for item in tool_schemas(session)}
    assert "save_to_corpus" not in scholarly["search_scholarly_literature"]["parameters"]["properties"]
    assert "save_to_corpus" not in scholarly["search_patent_literature"]["parameters"]["properties"]


def test_mode_does_not_leak_across_sessions(tmp_path, monkeypatch):
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    app_a, _buf_a = _app(tmp_path, monkeypatch, session_id="lit-a")
    assert app_a.handle_command("/literature corpus") is False
    assert app_a.session.get("literature_mode") == {"mode": "corpus"}
    app_b, buf_b = _app(tmp_path, monkeypatch, session_id="lit-b")
    assert "literature_mode" not in app_b.session
    assert _literature_names(app_b.session) == set()
    assert app_b.handle_command("/literature") is False
    assert "literature_mode=off" in buf_b.getvalue()
    assert app_a.handle_command("/clear") is False
    assert "literature_mode" not in app_a.session
    assert "handle_command" not in inspect.getsource(app_a.ask)


def test_dispatch_ingest_refuses_in_every_mode():
    from dissolve.session import bind_tool_session, new_session

    for stored in (None, {"mode": "corpus"}, {"mode": "scholarly"}):
        record = new_session()
        if stored is not None:
            record["literature_mode"] = stored
        with bind_tool_session(record):
            ingest = dispatch("ingest_literature_documents", paths=["/tmp/nope.pdf"])
            graph = dispatch("ingest_literature_graph", paths=["/tmp/nope.json"])
        assert ingest["available"] is False
        assert ingest["refusal"] == "literature_tools_not_offered"
        assert graph["available"] is False
        assert graph["refusal"] == "literature_tools_not_offered"


def test_slash_sets_and_on_does_not_write(tmp_path, monkeypatch):
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    app, buf = _app(tmp_path, monkeypatch)
    assert app.handle_command("/literature scholarly") is False
    assert app.session.get("literature_mode") == {"mode": "scholarly"}
    assert app.handle_command("/literature on") is False
    assert app.session.get("literature_mode") == {"mode": "scholarly"}
    assert "usage: /literature" in buf.getvalue()
    assert app.handle_command("/literature off") is False
    assert app.session.get("literature_mode") == {"mode": "off"}
