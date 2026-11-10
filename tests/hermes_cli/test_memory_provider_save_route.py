"""Save route for a memory provider that is on disk but cannot be imported.

``PUT /api/memory/providers/{name}/config`` used to answer 404 Unknown for a
provider that exists on disk but fails to import (missing deps / stale venv),
even though the setup route tolerates exactly that case. A 404 sends the user
looking for a provider that is already installed, so save must answer 400 with
the setup-first hint — and must never mask a readiness refusal with it.
"""

from __future__ import annotations

import pytest


@pytest.fixture()
def client(_isolate_hermes_home):
    """Dashboard client carrying the session token (same shape as test_web_server.py)."""
    testclient = pytest.importorskip("starlette.testclient")
    from hermes_cli.web_server import _SESSION_HEADER_NAME, _SESSION_TOKEN, app

    test_client = testclient.TestClient(app)
    test_client.headers[_SESSION_HEADER_NAME] = _SESSION_TOKEN
    return test_client


def test_unimportable_installed_provider_is_400_not_404(client, monkeypatch):
    import hermes_cli.web_routers.memory_providers as mp_routes

    monkeypatch.setattr(mp_routes, "_load_memory_provider", lambda name: None)
    monkeypatch.setattr(mp_routes, "_memory_provider_manifest", lambda name: {"name": name})
    monkeypatch.setattr(mp_routes, "_require_memory_provider_ready", lambda name: None)

    resp = client.put("/api/memory/providers/flatprov/config", json={"values": {"mode": "cloud"}})

    assert resp.status_code == 400
    assert "failed to load" in resp.json()["detail"]


def test_readiness_refusal_survives_the_installed_check(client, monkeypatch):
    """The not-ready 400 (setup-first, provider-specific) wins over the generic one."""
    from fastapi import HTTPException

    import hermes_cli.web_routers.memory_providers as mp_routes

    def refuse(name):
        raise HTTPException(status_code=400, detail=f"Memory provider '{name}' is not ready (needs setup).")

    monkeypatch.setattr(mp_routes, "_load_memory_provider", lambda name: None)
    monkeypatch.setattr(mp_routes, "_memory_provider_manifest", lambda name: {"name": name})
    monkeypatch.setattr(mp_routes, "_require_memory_provider_ready", refuse)

    resp = client.put("/api/memory/providers/flatprov/config", json={"values": {"mode": "cloud"}})

    assert resp.status_code == 400
    assert "not ready" in resp.json()["detail"]


def test_unknown_provider_still_404s(client, monkeypatch):
    import hermes_cli.web_routers.memory_providers as mp_routes

    monkeypatch.setattr(mp_routes, "_load_memory_provider", lambda name: None)
    monkeypatch.setattr(mp_routes, "_memory_provider_manifest", lambda name: None)

    resp = client.put("/api/memory/providers/nosuch/config", json={"values": {"mode": "cloud"}})

    assert resp.status_code == 404
