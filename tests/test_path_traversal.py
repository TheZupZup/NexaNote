"""
Synthetic `md.<base64(stem)>` ids address plain Markdown files by name, so a
forged id must not reach files outside the notes directory.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from fastapi.testclient import TestClient

from nexanote.api.routes import create_app
from nexanote.storage import FileNoteStore, PlainMarkdownNoteStore
from nexanote.storage.file_store import plain_md_id_from_stem


@pytest.fixture(params=[FileNoteStore, PlainMarkdownNoteStore])
def api(request, tmp_path):
    db = request.param(tmp_path / "data")
    with TestClient(create_app(db), raise_server_exceptions=False) as c:
        yield c, db
    db.close()


@pytest.mark.parametrize("stem", ["../../outside_secret", "..\\..\\outside_secret", ".."])
def test_plain_md_id_cannot_escape_the_notes_dir(api, tmp_path, stem):
    c, db = api
    (tmp_path / "outside_secret.md").write_text("TOP SECRET", encoding="utf-8")
    evil_id = plain_md_id_from_stem(stem)

    assert c.get(f"/notes/{evil_id}").status_code == 404
    assert c.put(f"/notes/{evil_id}/pages/1/text",
                 json={"typed_content": "pwned"}).status_code == 404
    assert (tmp_path / "outside_secret.md").read_text(encoding="utf-8") == "TOP SECRET"
