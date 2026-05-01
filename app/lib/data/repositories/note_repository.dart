import 'package:sqflite/sqflite.dart';
import 'package:uuid/uuid.dart';

import '../models/notebook.dart';
import '../models/note.dart';
import '../models/stroke.dart';
import '../models/point.dart';

/// Local CRUD operations for notebooks, notes, and ink strokes.
///
/// Takes a [Database] directly so it is easy to test with an in-memory DB:
///
///   final db = await openDatabase(inMemoryDatabasePath, ...);
///   final repo = NoteRepository(db);
///
/// Does not touch [AppState] or [ApiClient]. The existing Linux/API flow
/// is unaffected; this repository is purely additive for Phase 2.
class NoteRepository {
  final Database _db;
  final Uuid _uuid;

  NoteRepository(this._db, {Uuid? uuid}) : _uuid = uuid ?? const Uuid();

  // -----------------------------------------------------------------------
  // Notebooks
  // -----------------------------------------------------------------------

  /// Creates a notebook and persists it. Returns the saved [Notebook].
  Future<Notebook> createNotebook(
    String name, {
    String? parentId,
    String color = '#6366f1',
    String description = '',
    String icon = 'notebook',
  }) async {
    final now = DateTime.now().toUtc();
    final notebook = Notebook(
      id: _uuid.v4(),
      parentId: parentId,
      name: name,
      description: description,
      color: color,
      icon: icon,
      createdAt: now,
      updatedAt: now,
    );
    await _db.insert('notebooks', notebook.toMap());
    return notebook;
  }

  /// Returns all non-archived notebooks ordered by name.
  Future<List<Notebook>> getNotebooks({bool includeArchived = false}) async {
    final where = includeArchived ? null : 'is_archived = 0';
    final rows = await _db.query(
      'notebooks',
      where: where,
      orderBy: 'name ASC',
    );
    return rows.map(Notebook.fromMap).toList();
  }

  // -----------------------------------------------------------------------
  // Notes
  // -----------------------------------------------------------------------

  /// Creates a note and persists it. Returns the saved [Note].
  Future<Note> createNote(
    String title, {
    String? notebookId,
    String noteType = 'typed',
    String typedContent = '',
    List<String> tags = const [],
  }) async {
    final now = DateTime.now().toUtc();
    final note = Note(
      id: _uuid.v4(),
      notebookId: notebookId,
      title: title,
      noteType: noteType,
      typedContent: typedContent,
      tags: tags,
      createdAt: now,
      updatedAt: now,
    );
    await _db.insert('notes', note.toMap());
    return note;
  }

  /// Returns non-deleted, non-archived notes for a notebook, newest first.
  Future<List<Note>> getNotesForNotebook(String notebookId) async {
    final rows = await _db.query(
      'notes',
      where: 'notebook_id = ? AND is_deleted = 0 AND is_archived = 0',
      whereArgs: [notebookId],
      orderBy: 'updated_at DESC',
    );
    return rows.map(Note.fromMap).toList();
  }

  /// Returns a single note by [id], or null if not found.
  Future<Note?> getNoteById(String id) async {
    final rows = await _db.query(
      'notes',
      where: 'id = ?',
      whereArgs: [id],
      limit: 1,
    );
    if (rows.isEmpty) return null;
    return Note.fromMap(rows.first);
  }

  /// Soft-deletes a note by setting [is_deleted = 1] and marking it modified.
  Future<void> deleteNote(String id) async {
    final now = DateTime.now().toUtc().toIso8601String();
    await _db.update(
      'notes',
      {
        'is_deleted': 1,
        'sync_status': 'modified',
        'updated_at': now,
      },
      where: 'id = ?',
      whereArgs: [id],
    );
  }

  /// Returns every non-archived note across all notebooks, newest first.
  Future<List<Note>> getAllNotes({bool includeDeleted = false}) async {
    final where = includeDeleted ? null : 'is_deleted = 0 AND is_archived = 0';
    final rows = await _db.query(
      'notes',
      where: where,
      orderBy: 'updated_at DESC',
    );
    return rows.map(Note.fromMap).toList();
  }

  /// Wipes notebooks/notes/strokes and inserts the supplied records in a
  /// single transaction. Used by SyncService when replacing the local store
  /// with the backend's authoritative state.
  Future<void> replaceAll({
    required List<Notebook> notebooks,
    required List<Note> notes,
  }) async {
    await _db.transaction((txn) async {
      await txn.delete('stroke_points');
      await txn.delete('strokes');
      await txn.delete('notes');
      await txn.delete('notebooks');
      for (final nb in notebooks) {
        await txn.insert('notebooks', nb.toMap());
      }
      for (final note in notes) {
        await txn.insert('notes', note.toMap());
      }
    });
  }

  // -----------------------------------------------------------------------
  // Strokes
  // -----------------------------------------------------------------------

  /// Saves a stroke and all its points. Replaces any existing stroke with
  /// the same [id] (upsert behaviour).
  Future<void> saveStroke(Stroke stroke) async {
    await _db.insert(
      'strokes',
      stroke.toMap(),
      conflictAlgorithm: ConflictAlgorithm.replace,
    );

    // Remove old points first so re-saves don't accumulate duplicates.
    await _db.delete(
      'stroke_points',
      where: 'stroke_id = ?',
      whereArgs: [stroke.id],
    );

    for (var i = 0; i < stroke.points.length; i++) {
      await _db.insert(
        'stroke_points',
        stroke.points[i].toMap(stroke.id, i),
      );
    }
  }

  /// Returns all strokes for [noteId] with their points, ordered by creation.
  Future<List<Stroke>> getStrokesForNote(String noteId) async {
    final strokeRows = await _db.query(
      'strokes',
      where: 'note_id = ?',
      whereArgs: [noteId],
      orderBy: 'created_at ASC',
    );

    final strokes = <Stroke>[];
    for (final strokeRow in strokeRows) {
      final strokeId = strokeRow['id'] as String;
      final pointRows = await _db.query(
        'stroke_points',
        where: 'stroke_id = ?',
        whereArgs: [strokeId],
        orderBy: 'seq ASC',
      );
      final points = pointRows.map(StrokePoint.fromMap).toList();
      strokes.add(Stroke.fromMap(strokeRow, points: points));
    }
    return strokes;
  }
}
