import 'package:sqflite/sqflite.dart';

/// SQL schema for the local NexaNote SQLite database.
///
/// Table hierarchy:
///   notebooks → notes → strokes → stroke_points
///
/// Mirrors the Python schema in nexanote/storage/database.py.
/// Keep this file as the single source of truth for table definitions.
class Schema {
  /// Bumped to 2 when remote_id/remote_path columns were added so the
  /// SyncService can map a local note to its canonical .md file on the
  /// WebDAV/NAS without inventing a fresh row on every pull.
  ///
  /// Bumped to 3 for remote_baseline: the server's updated_at when our push
  /// created a note, so an interrupted upload is only resumed into a remote
  /// note that provably hasn't changed since.
  ///
  /// Bumped to 4 for the per-part edit revisions (title/text/ink `_rev` and
  /// `_synced_rev`): every local edit bumps its part's revision, and a part
  /// is waiting for the server while its revision is ahead of the last one
  /// the server confirmed. That makes SQLite the durable copy of an edit
  /// until the backend has acknowledged it, even in connected mode. Also
  /// adds `has_content` and `blind_edit` (see Note.hasContent).
  static const int version = 4;

  static const String _createNotebooks = '''
    CREATE TABLE IF NOT EXISTS notebooks (
      id          TEXT PRIMARY KEY,
      parent_id   TEXT,
      name        TEXT NOT NULL DEFAULT 'New Notebook',
      description TEXT NOT NULL DEFAULT '',
      color       TEXT NOT NULL DEFAULT '#6366f1',
      icon        TEXT NOT NULL DEFAULT 'notebook',
      is_archived INTEGER NOT NULL DEFAULT 0,
      sync_status TEXT NOT NULL DEFAULT 'local_only',
      created_at  TEXT NOT NULL,
      updated_at  TEXT NOT NULL,
      FOREIGN KEY (parent_id) REFERENCES notebooks(id)
    )
  ''';

  static const String _createNotes = '''
    CREATE TABLE IF NOT EXISTS notes (
      id            TEXT PRIMARY KEY,
      notebook_id   TEXT,
      title         TEXT NOT NULL DEFAULT 'Untitled',
      note_type     TEXT NOT NULL DEFAULT 'typed',
      tags          TEXT NOT NULL DEFAULT '[]',
      typed_content TEXT NOT NULL DEFAULT '',
      is_pinned     INTEGER NOT NULL DEFAULT 0,
      is_archived   INTEGER NOT NULL DEFAULT 0,
      is_deleted    INTEGER NOT NULL DEFAULT 0,
      sync_status   TEXT NOT NULL DEFAULT 'local_only',
      remote_id     TEXT,
      remote_path   TEXT,
      remote_baseline TEXT,
      title_rev        INTEGER NOT NULL DEFAULT 0,
      title_synced_rev INTEGER NOT NULL DEFAULT 0,
      text_rev         INTEGER NOT NULL DEFAULT 0,
      text_synced_rev  INTEGER NOT NULL DEFAULT 0,
      ink_rev          INTEGER NOT NULL DEFAULT 0,
      ink_synced_rev   INTEGER NOT NULL DEFAULT 0,
      has_content   INTEGER NOT NULL DEFAULT 1,
      blind_edit    INTEGER NOT NULL DEFAULT 0,
      created_at    TEXT NOT NULL,
      updated_at    TEXT NOT NULL,
      FOREIGN KEY (notebook_id) REFERENCES notebooks(id)
    )
  ''';

  /// Every column added by version 4.
  static const v4Columns = [...editRevisionColumns, 'has_content', 'blind_edit'];

  static const editRevisionColumns = [
    'title_rev',
    'title_synced_rev',
    'text_rev',
    'text_synced_rev',
    'ink_rev',
    'ink_synced_rev',
  ];

  static const String _createStrokes = '''
    CREATE TABLE IF NOT EXISTS strokes (
      id         TEXT PRIMARY KEY,
      note_id    TEXT NOT NULL,
      color      TEXT NOT NULL DEFAULT '#000000',
      width      REAL NOT NULL DEFAULT 2.0,
      tool       TEXT NOT NULL DEFAULT 'pen',
      created_at TEXT NOT NULL,
      FOREIGN KEY (note_id) REFERENCES notes(id) ON DELETE CASCADE
    )
  ''';

  /// Stores individual captured points for each stroke.
  /// [seq] preserves the draw order within a stroke.
  static const String _createStrokePoints = '''
    CREATE TABLE IF NOT EXISTS stroke_points (
      id           INTEGER PRIMARY KEY AUTOINCREMENT,
      stroke_id    TEXT NOT NULL,
      x            REAL NOT NULL,
      y            REAL NOT NULL,
      pressure     REAL NOT NULL DEFAULT 0.5,
      timestamp_ms INTEGER NOT NULL DEFAULT 0,
      seq          INTEGER NOT NULL DEFAULT 0,
      FOREIGN KEY (stroke_id) REFERENCES strokes(id) ON DELETE CASCADE
    )
  ''';

  static const String _indexNotesNotebook =
      'CREATE INDEX IF NOT EXISTS idx_notes_notebook ON notes(notebook_id)';

  static const String _indexNotesUpdated =
      'CREATE INDEX IF NOT EXISTS idx_notes_updated ON notes(updated_at)';

  static const String _indexNotesRemoteId =
      'CREATE INDEX IF NOT EXISTS idx_notes_remote_id ON notes(remote_id)';

  static const String _indexNotesRemotePath =
      'CREATE INDEX IF NOT EXISTS idx_notes_remote_path ON notes(remote_path)';

  static const String _indexStrokesNote =
      'CREATE INDEX IF NOT EXISTS idx_strokes_note ON strokes(note_id)';

  static const String _indexStrokePointsStroke =
      'CREATE INDEX IF NOT EXISTS idx_stroke_points_stroke ON stroke_points(stroke_id, seq)';

  /// Called by [openDatabase] onCreate. Creates all tables and indexes.
  static Future<void> onCreate(Database db, int version) async {
    await db.execute(_createNotebooks);
    await db.execute(_createNotes);
    await db.execute(_createStrokes);
    await db.execute(_createStrokePoints);
    await db.execute(_indexNotesNotebook);
    await db.execute(_indexNotesUpdated);
    await db.execute(_indexNotesRemoteId);
    await db.execute(_indexNotesRemotePath);
    await db.execute(_indexStrokesNote);
    await db.execute(_indexStrokePointsStroke);
  }

  /// Forward-only migrations. Each `if` block applies the steps needed to
  /// reach the next version; SQLite's ALTER TABLE ADD COLUMN is enough for
  /// the nullable remote_id/remote_path additions in v2.
  static Future<void> onUpgrade(
    Database db,
    int oldVersion,
    int newVersion,
  ) async {
    if (oldVersion < 2) {
      await db.execute('ALTER TABLE notes ADD COLUMN remote_id TEXT');
      await db.execute('ALTER TABLE notes ADD COLUMN remote_path TEXT');
      await db.execute(_indexNotesRemoteId);
      await db.execute(_indexNotesRemotePath);
    }
    if (oldVersion < 3) {
      await db.execute('ALTER TABLE notes ADD COLUMN remote_baseline TEXT');
    }
    if (oldVersion < 4) {
      for (final column in editRevisionColumns) {
        await db.execute(
            'ALTER TABLE notes ADD COLUMN $column INTEGER NOT NULL DEFAULT 0');
      }
      await db.execute(
          'ALTER TABLE notes ADD COLUMN has_content INTEGER NOT NULL DEFAULT 1');
      await db.execute(
          'ALTER TABLE notes ADD COLUMN blind_edit INTEGER NOT NULL DEFAULT 0');
      // Rows a pull created carry the server id as their own id and hold
      // no text or drawing.
      await db.execute(
          'UPDATE notes SET has_content = 0 WHERE remote_id = id '
          "AND sync_status != 'local_only'");
      // Older builds left local edits of server notes as `modified` without
      // saying which part changed: treat every part as waiting to be sent,
      // so they are neither lost to a refresh nor marked synced unsent.
      await db.execute(
          'UPDATE notes SET title_rev = 1, text_rev = 1, ink_rev = 1 '
          "WHERE sync_status = 'modified' AND remote_id IS NOT NULL");
      // Those edits, when made on a row without content, were made blind.
      await db.execute(
          'UPDATE notes SET blind_edit = 1 '
          "WHERE has_content = 0 AND sync_status = 'modified'");
    }
  }
}
