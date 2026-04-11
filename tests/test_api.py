"""
Tests NexaNote — API REST
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from fastapi.testclient import TestClient

from nexanote.models.note import Note, Notebook, NoteType
from nexanote.storage.database import NexaNoteDB
from nexanote.api.routes import create_app


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def client(tmp_path):
    db = NexaNoteDB(tmp_path / "test_api.db")
    app = create_app(db)
    with TestClient(app) as c:
        yield c
    db.close()


@pytest.fixture
def client_with_data(tmp_path):
    db = NexaNoteDB(tmp_path / "test_api_data.db")

    nb = Notebook(name="Physique quantique", color="#8b5cf6")
    db.save_notebook(nb)

    note = Note(notebook_id=nb.id, title="Dualité onde-corpuscule")
    note.add_tag("physique")
    page = note.add_page(template="lined")
    page.typed_content = "La lumière se comporte à la fois comme une onde et un corpuscule."
    db.save_note(note)

    app = create_app(db)
    with TestClient(app) as c:
        yield c, nb, note
    db.close()


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------

class TestHealth:
    def test_health_returns_ok(self, client):
        resp = client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert "stats" in data
        assert "version" in data


# ---------------------------------------------------------------------------
# Notebooks
# ---------------------------------------------------------------------------

class TestNotebooks:
    def test_list_empty(self, client):
        resp = client.get("/notebooks")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_create_notebook(self, client):
        resp = client.post("/notebooks", json={
            "name": "Mon carnet",
            "color": "#ff5733",
        })
        assert resp.status_code == 201
        data = resp.json()
        assert data["name"] == "Mon carnet"
        assert data["color"] == "#ff5733"
        assert "id" in data

    def test_get_notebook(self, client):
        created = client.post("/notebooks", json={"name": "Test"}).json()
        resp = client.get(f"/notebooks/{created['id']}")
        assert resp.status_code == 200
        assert resp.json()["id"] == created["id"]

    def test_get_notebook_not_found(self, client):
        resp = client.get("/notebooks/inexistant")
        assert resp.status_code == 404

    def test_update_notebook(self, client):
        created = client.post("/notebooks", json={"name": "Ancien nom"}).json()
        resp = client.put(f"/notebooks/{created['id']}", json={"name": "Nouveau nom"})
        assert resp.status_code == 200
        assert resp.json()["name"] == "Nouveau nom"

    def test_delete_notebook(self, client):
        created = client.post("/notebooks", json={"name": "À supprimer"}).json()
        resp = client.delete(f"/notebooks/{created['id']}")
        assert resp.status_code == 204
        resp2 = client.get(f"/notebooks/{created['id']}")
        assert resp2.status_code == 404

    def test_list_returns_all(self, client):
        for i in range(3):
            client.post("/notebooks", json={"name": f"Carnet {i}"})
        resp = client.get("/notebooks")
        assert len(resp.json()) == 3


# ---------------------------------------------------------------------------
# Notes
# ---------------------------------------------------------------------------

class TestNotes:
    def test_create_note(self, client_with_data):
        c, nb, _ = client_with_data
        resp = c.post("/notes", json={
            "title": "Mécanique quantique",
            "note_type": "typed",
            "notebook_id": nb.id,
            "tags": ["physique", "quantique"],
        })
        assert resp.status_code == 201
        data = resp.json()
        assert data["title"] == "Mécanique quantique"
        assert "physique" in data["tags"]
        assert data["notebook_id"] == nb.id

    def test_create_note_invalid_notebook(self, client):
        resp = client.post("/notes", json={
            "title": "Test",
            "notebook_id": "inexistant",
        })
        assert resp.status_code == 404

    def test_get_note_with_pages(self, client_with_data):
        c, nb, note = client_with_data
        resp = c.get(f"/notes/{note.id}?pages=true")
        assert resp.status_code == 200
        data = resp.json()
        assert data["title"] == "Dualité onde-corpuscule"
        assert data["pages"] is not None
        assert len(data["pages"]) == 1
        assert "lumière" in data["pages"][0]["typed_content"]

    def test_update_note_title(self, client_with_data):
        c, nb, note = client_with_data
        resp = c.put(f"/notes/{note.id}", json={"title": "Nouveau titre"})
        assert resp.status_code == 200
        assert resp.json()["title"] == "Nouveau titre"

    def test_soft_delete_note(self, client_with_data):
        c, nb, note = client_with_data
        resp = c.delete(f"/notes/{note.id}")
        assert resp.status_code == 204

        # La note ne doit plus apparaître dans la liste normale
        list_resp = c.get("/notes")
        ids = [n["id"] for n in list_resp.json()]
        assert note.id not in ids

        # Mais visible avec include_deleted
        deleted_resp = c.get("/notes?include_deleted=true")
        ids_deleted = [n["id"] for n in deleted_resp.json()]
        assert note.id in ids_deleted

    def test_restore_note(self, client_with_data):
        c, nb, note = client_with_data
        c.delete(f"/notes/{note.id}")
        resp = c.post(f"/notes/{note.id}/restore")
        assert resp.status_code == 200
        assert resp.json()["is_deleted"] is False

    def test_duplicate_note(self, client_with_data):
        c, nb, note = client_with_data
        resp = c.post(f"/notes/{note.id}/duplicate")
        assert resp.status_code == 201
        dupe = resp.json()
        assert dupe["id"] != note.id
        assert "copie" in dupe["title"]

    def test_filter_by_notebook(self, client_with_data):
        c, nb, note = client_with_data
        resp = c.get(f"/notes?notebook_id={nb.id}")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["id"] == note.id


# ---------------------------------------------------------------------------
# Pages — encre et texte
# ---------------------------------------------------------------------------

class TestPages:
    def test_get_page(self, client_with_data):
        c, nb, note = client_with_data
        resp = c.get(f"/notes/{note.id}/pages/1")
        assert resp.status_code == 200
        data = resp.json()
        assert data["page_number"] == 1
        assert "lumière" in data["typed_content"]

    def test_update_text(self, client_with_data):
        c, nb, note = client_with_data
        resp = c.put(
            f"/notes/{note.id}/pages/1/text",
            json={"typed_content": "Nouveau contenu texte mis à jour."},
        )
        assert resp.status_code == 200
        assert resp.json()["typed_content"] == "Nouveau contenu texte mis à jour."

    def test_update_ink(self, client_with_data):
        c, nb, note = client_with_data
        strokes = [
            {
                "id": "stroke-test-001",
                "color": "#0000ff",
                "width": 3.0,
                "tool": "pen",
                "points": [
                    {"x": 10.0, "y": 20.0, "pressure": 0.5, "ts": 0},
                    {"x": 50.0, "y": 80.0, "pressure": 0.8, "ts": 100},
                    {"x": 90.0, "y": 40.0, "pressure": 0.6, "ts": 200},
                ],
            }
        ]
        resp = c.put(
            f"/notes/{note.id}/pages/1/ink",
            json={"strokes": strokes},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["strokes"]) == 1
        assert data["strokes"][0]["id"] == "stroke-test-001"
        assert data["strokes"][0]["color"] == "#0000ff"

    def test_update_ink_replaces_all_strokes(self, client_with_data):
        """Un PUT /ink remplace TOUS les strokes — comportement attendu."""
        c, nb, note = client_with_data

        # Premier PUT — 2 strokes
        strokes_1 = [
            {"id": f"s{i}", "color": "#000", "width": 2.0, "tool": "pen",
             "points": [{"x": float(i), "y": float(i), "pressure": 0.5, "ts": 0},
                        {"x": float(i+10), "y": float(i+10), "pressure": 0.5, "ts": 10}]}
            for i in range(2)
        ]
        c.put(f"/notes/{note.id}/pages/1/ink", json={"strokes": strokes_1})

        # Deuxième PUT — 1 seul stroke
        strokes_2 = [
            {"id": "only-one", "color": "#ff0000", "width": 2.0, "tool": "pen",
             "points": [{"x": 0.0, "y": 0.0, "pressure": 0.5, "ts": 0},
                        {"x": 10.0, "y": 10.0, "pressure": 0.5, "ts": 10}]}
        ]
        resp = c.put(f"/notes/{note.id}/pages/1/ink", json={"strokes": strokes_2})
        assert len(resp.json()["strokes"]) == 1  # Bien remplacé, pas accumulé


# ---------------------------------------------------------------------------
# Recherche et stats
# ---------------------------------------------------------------------------

class TestSearchAndStats:
    def test_search_by_title(self, client_with_data):
        c, nb, note = client_with_data
        resp = c.get("/search?q=onde")
        assert resp.status_code == 200
        results = resp.json()
        assert len(results) >= 1
        assert any("onde" in r["title"].lower() for r in results)

    def test_search_no_results(self, client_with_data):
        c, *_ = client_with_data
        resp = c.get("/search?q=xyzinexistant")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_stats(self, client_with_data):
        c, nb, note = client_with_data
        resp = c.get("/stats")
        assert resp.status_code == 200
        data = resp.json()
        assert data["notebooks"] >= 1
        assert data["notes"] >= 1


# ---------------------------------------------------------------------------
# Sync configuration
# ---------------------------------------------------------------------------

class TestSync:
    def test_configure_sync(self, client):
        resp = client.post("/sync/configure", json={
            "server_url": "http://localhost:8765/",
            "username": "nexanote",
            "password": "nexanote",
        })
        assert resp.status_code == 200
        assert resp.json()["status"] == "configured"

    def test_trigger_sync_without_config(self, client):
        resp = client.post("/sync/trigger")
        assert resp.status_code == 400

    def test_sync_status_empty(self, client):
        resp = client.get("/sync/status")
        assert resp.status_code == 200
        assert resp.json()["status"] == "never_synced"
