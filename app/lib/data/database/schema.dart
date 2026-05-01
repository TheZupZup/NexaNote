import 'package:sqflite/sqflite.dart';

/// SQL schema for the local NexaNote SQLite database.
///
/// Table hierarchy:
///   notebooks → notes → strokes → stroke_points
///
/// Mirrors the Python schema in nexanote/storage/database.py.
/// Keep this file as the single source of truth for table definitions.
class Schema {
  static const int version = 1;

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
      created_at    TEXT NOT NULL,
      updated_at    TEXT NOT NULL,
      FOREIGN KEY (notebook_id) REFERENCES notebooks(id)
    )
  ''';

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
    await db.execute(_indexStrokesNote);
    await db.execute(_indexStrokePointsStroke);
  }
}
