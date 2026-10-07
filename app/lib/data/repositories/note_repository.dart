import 'dart:convert';

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

  /// Returns the local note linked to [remoteId], or null if no local row
  /// has been adopted for that remote yet. Used by SyncService to decide
  /// between adopting an existing local row and inserting a new one.
  Future<Note?> getNoteByRemoteId(String remoteId) async {
    final rows = await _db.query(
      'notes',
      where: 'remote_id = ?',
      whereArgs: [remoteId],
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

  /// Inserts [note] or replaces the existing row with the same primary key.
  ///
  /// Used by the sync engine to adopt a remote .md file into the local DB
  /// without going through the duplicating `INSERT` path of [createNote].
  Future<void> upsertNote(Note note) async {
    await _db.insert(
      'notes',
      note.toMap(),
      conflictAlgorithm: ConflictAlgorithm.replace,
    );
  }

  /// Records a local edit to note [id]: writes only the given [fields],
  /// bumps `updated_at` and flags a synced note as `modified`.
  ///
  /// A single UPDATE instead of read-modify-write via [upsertNote]: the editor
  /// saves title, text and ink independently, and a full-row upsert built from
  /// an earlier read would put back stale copies of the other fields.
  ///
  /// Returns the number of rows changed: 0 when note [id] doesn't exist.
  ///
  /// [blind] says the edit was made without the note's real content at hand
  /// (local mode): on a row without content it sets [Note.blindEdit].
  Future<int> updateNoteFields(
    String id, [
    Map<String, Object?> fields = const {},
    NotePart? edited,
    bool blind = false,
  ]) =>
      _updateNoteFields(_db, id, fields, edited, blind);

  static Future<int> _updateNoteFields(
    DatabaseExecutor db,
    String id,
    Map<String, Object?> fields,
    NotePart? edited, [
    bool blind = false,
  ]) async {
    final assignments = [
      for (final column in fields.keys) '$column = ?',
      if (edited != null) '${edited.revColumn} = ${edited.revColumn} + 1',
      if (blind) 'blind_edit = CASE has_content WHEN 0 THEN 1 ELSE blind_edit END',
      'updated_at = ?',
      "sync_status = CASE sync_status WHEN 'synced' THEN 'modified' "
          'ELSE sync_status END',
    ];
    return db.rawUpdate(
      'UPDATE notes SET ${assignments.join(', ')} WHERE id = ?',
      [
        ...fields.values,
        DateTime.now().toUtc().toIso8601String(),
        id,
      ],
    );
  }

  /// Durably records a new drawing for note [id]: replaces its strokes and
  /// bumps the ink revision in one transaction, so the stored drawing and
  /// its revision always match. Returns the new ink revision, or null when
  /// note [id] doesn't exist (nothing is written then).
  Future<int?> recordInk(String id, List<Stroke> strokes,
      {bool blind = false}) {
    return _db.transaction((txn) async {
      if (await _updateNoteFields(txn, id, const {}, NotePart.ink, blind) ==
          0) {
        return null;
      }
      await _replaceStrokes(txn, id, strokes);
      final rows = await txn.query('notes',
          columns: ['ink_rev'], where: 'id = ?', whereArgs: [id]);
      return rows.first['ink_rev'] as int;
    });
  }

  /// The current ink revision of note [id] with the drawing it stands for,
  /// read together so they can't come from two different saves.
  Future<({int rev, List<Stroke> strokes})?> readInk(String id) {
    return _db.transaction((txn) async {
      final rows = await txn.query('notes',
          columns: ['ink_rev'], where: 'id = ?', whereArgs: [id], limit: 1);
      if (rows.isEmpty) return null;
      return (
        rev: rows.first['ink_rev'] as int,
        strokes: await _strokesForNote(txn, id),
      );
    });
  }

  /// Records that the server has [part] of note [id] up to revision [rev].
  /// Never moves backwards, so an older save finishing late can't mark a
  /// newer edit as synced. A connected-mode copy with nothing left to send
  /// goes back to `synced`.
  Future<void> markPartSynced(String id, NotePart part, int rev) async {
    await _db.transaction((txn) async {
      await txn.rawUpdate(
        'UPDATE notes SET ${part.syncedRevColumn} = '
        'MAX(${part.syncedRevColumn}, ?) WHERE id = ?',
        [rev, id],
      );
      await txn.rawUpdate(
        "UPDATE notes SET sync_status = 'synced' "
        "WHERE id = ? AND sync_status = 'modified' AND remote_id IS NOT NULL "
        'AND title_rev <= title_synced_rev AND text_rev <= text_synced_rev '
        'AND ink_rev <= ink_synced_rev',
        [id],
      );
    });
  }

  /// Id of the local row that holds remote note [remoteId], or null. A row
  /// still waiting to finish its first upload (`local_only`) counts too,
  /// after any other: it is the local copy of that note, and edits recorded
  /// into it are uploaded by SyncService.pushLocal with its usual checks.
  ///
  /// Falls back to a row whose own id is [remoteId]: notes listed from the
  /// local store while the backend is down are opened by their local id.
  Future<String?> localIdForRemote(String remoteId) =>
      _localIdForRemote(_db, remoteId);

  static Future<String?> _localIdForRemote(
      DatabaseExecutor db, String remoteId) async {
    final rows = await db.rawQuery(
      'SELECT id FROM notes WHERE remote_id = ? '
      "ORDER BY sync_status = 'local_only', id = remote_id DESC LIMIT 1",
      [remoteId],
    );
    if (rows.isNotEmpty) return rows.first['id'] as String;
    final own = await db.query('notes',
        columns: ['id'], where: 'id = ?', whereArgs: [remoteId], limit: 1);
    return own.isEmpty ? null : remoteId;
  }

  /// Stores the server's copy of a note (opened in connected mode) as the
  /// local copy of it, so it can be read without the backend, and returns
  /// the local row's id. [remote] carries the server id as both `id` and
  /// `remoteId`.
  ///
  /// Parts with a local edit still waiting for the server keep the local
  /// version. With [refresh] false an existing copy is left as it is (used
  /// to make sure a row exists before recording an edit). Rows that aren't
  /// a plain copy of the server note (`conflict`, `local_only`) are never
  /// overwritten. A row without content (see [Note.hasContent]) gets the
  /// server's; text or ink edited into it first are split off into a note
  /// of their own, since they were made without seeing the real content.
  Future<String> cacheRemoteNote(
    Note remote,
    List<Stroke> strokes, {
    bool refresh = true,
  }) {
    return _db.transaction((txn) async {
      final localId = await _localIdForRemote(txn, remote.id);
      if (localId == null) {
        await txn.insert('notes', remote.toMap());
        await _replaceStrokes(txn, remote.id, strokes);
        return remote.id;
      }
      if (!refresh) return localId;
      final rows = await txn.query('notes',
          where: 'id = ?', whereArgs: [localId], limit: 1);
      var local = Note.fromMap(rows.first);
      final tracked = local.syncStatus == 'synced' ||
          (local.syncStatus == 'modified' && local.hasPendingEdits);
      if (!tracked) return localId;
      if (local.blindEdit) {
        await _splitOffContentlessEdits(txn, local);
        local = Note.fromMap((await txn.query('notes',
                where: 'id = ?', whereArgs: [localId], limit: 1))
            .first);
      }
      await txn.update(
        'notes',
        {
          'notebook_id': remote.notebookId,
          'note_type': remote.noteType,
          'tags': jsonEncode(remote.tags),
          'is_pinned': remote.isPinned ? 1 : 0,
          if (!local.titlePending) 'title': remote.title,
          if (!local.textPending) 'typed_content': remote.typedContent,
          'has_content': 1,
          if (!local.hasPendingEdits) ...{
            'sync_status': 'synced',
            'updated_at': remote.updatedAt.toIso8601String(),
          },
        },
        where: 'id = ?',
        whereArgs: [localId],
      );
      if (!local.inkPending) await _replaceStrokes(txn, localId, strokes);
      return localId;
    });
  }

  /// Text or ink edited blind into row [id] (see [Note.blindEdit]) can't be
  /// sent into the server note: they were made on an empty page and would
  /// wipe what is really there. They are split
  /// off into a new note of their own, uploaded like one taken offline.
  /// Returns whether anything was split off.
  Future<bool> splitOffContentlessEdits(String id) {
    return _db.transaction((txn) async {
      final rows =
          await txn.query('notes', where: 'id = ?', whereArgs: [id], limit: 1);
      if (rows.isEmpty) return false;
      return _splitOffContentlessEdits(txn, Note.fromMap(rows.first));
    });
  }

  Future<bool> _splitOffContentlessEdits(Transaction txn, Note row) async {
    if (!row.blindEdit) return false;
    if (!(row.textPending || row.inkPending)) {
      await txn.update('notes', {'blind_edit': 0},
          where: 'id = ?', whereArgs: [row.id]);
      return false;
    }
    final now = DateTime.now().toUtc();
    final copy = Note(
      id: _uuid.v4(),
      notebookId: row.notebookId,
      title: '${row.title} (offline copy)',
      noteType: row.noteType,
      tags: row.tags,
      typedContent: row.textPending ? row.typedContent : '',
      createdAt: now,
      updatedAt: now,
    );
    await txn.insert('notes', copy.toMap());
    if (row.inkPending) {
      await txn.update('strokes', {'note_id': copy.id},
          where: 'note_id = ?', whereArgs: [row.id]);
    }
    await txn.rawUpdate(
      "UPDATE notes SET typed_content = '', text_synced_rev = text_rev, "
      'ink_synced_rev = ink_rev, blind_edit = 0 WHERE id = ?',
      [row.id],
    );
    return true;
  }

  /// Applies a note's metadata from a pull to local row [localId]. Decided
  /// against the row as it is now, not as an earlier snapshot saw it: a row
  /// with an edit still waiting for the server, a row not uploaded yet, or a
  /// locally modified row newer than [pulled] is left alone. Content columns
  /// and edit revisions are never touched.
  Future<void> applyPulledNote(String localId, Note pulled) async {
    await _db.transaction((txn) async {
      final rows = await txn.query('notes',
          where: 'id = ?', whereArgs: [localId], limit: 1);
      if (rows.isEmpty) return;
      final local = Note.fromMap(rows.first);
      final keepLocal = local.syncStatus == 'local_only' ||
          local.hasPendingEdits ||
          (local.syncStatus == 'modified' &&
              local.updatedAt.isAfter(pulled.updatedAt));
      if (keepLocal) return;
      await txn.update(
        'notes',
        {
          'notebook_id': pulled.notebookId,
          'title': pulled.title,
          'note_type': pulled.noteType,
          'tags': jsonEncode(pulled.tags),
          'is_pinned': pulled.isPinned ? 1 : 0,
          'is_deleted': pulled.isDeleted ? 1 : 0,
          'sync_status': 'synced',
          'remote_id': pulled.remoteId,
          'remote_path': pulled.remotePath,
          'updated_at': pulled.updatedAt.toIso8601String(),
        },
        where: 'id = ?',
        whereArgs: [localId],
      );
    });
  }

  /// Unlinks note [id] from a remote note that no longer exists, keeping its
  /// content: it becomes a note to upload (`local_only`) like one taken
  /// offline, and SyncService.pushLocal creates it on the server again.
  Future<void> detachFromRemote(String id) async {
    await _db.rawUpdate(
      "UPDATE notes SET remote_id = NULL, remote_baseline = NULL, "
      "sync_status = 'local_only', title_synced_rev = 0, text_synced_rev = 0, "
      'ink_synced_rev = 0 WHERE id = ?',
      [id],
    );
  }

  /// Inserts [note] unless a row with its id already exists.
  Future<void> insertNoteIfAbsent(Note note) async {
    await _db.insert('notes', note.toMap(),
        conflictAlgorithm: ConflictAlgorithm.ignore);
  }

  /// Flags note [id] as conflicting, leaving everything else as it is.
  Future<void> markNoteConflict(String id) async {
    await _db.update('notes', {'sync_status': 'conflict'},
        where: 'id = ?', whereArgs: [id]);
  }

  /// Hard-deletes note [id] only if it is still `synced` with nothing
  /// waiting for the server, checked at delete time. Returns rows removed.
  Future<int> hardDeleteNoteIfSynced(String id) {
    return _db.delete(
      'notes',
      where: "id = ? AND sync_status = 'synced' "
          'AND title_rev <= title_synced_rev AND text_rev <= text_synced_rev '
          'AND ink_rev <= ink_synced_rev',
      whereArgs: [id],
    );
  }

  /// Notes linked to a remote note with an edit the server hasn't confirmed
  /// yet. Notes taken in local mode and not uploaded yet
  /// (`local_only`) go through SyncService.pushLocal instead.
  Future<List<Note>> getNotesWithPendingEdits() async {
    final rows = await _db.query(
      'notes',
      where: "remote_id IS NOT NULL AND sync_status != 'local_only' "
          'AND is_deleted = 0 '
          'AND (title_rev > title_synced_rev OR text_rev > text_synced_rev '
          'OR ink_rev > ink_synced_rev)',
    );
    return rows.map(Note.fromMap).toList();
  }

  /// Links local note [id] to the note the server created for it, keeping it
  /// pending (`local_only`) until its content has been uploaded too.
  /// [baseline] is the server's updated_at after our create or our latest
  /// write into it (see [Note.remoteBaseline]); null when the server didn't
  /// report one.
  Future<void> setNoteRemoteId(
      String id, String remoteId, String? baseline) async {
    await _db.update(
        'notes', {'remote_id': remoteId, 'remote_baseline': baseline},
        where: 'id = ?', whereArgs: [id]);
  }

  /// Marks note [id] as synced, unless it was edited after [seenUpdatedAt]
  /// (the version that was uploaded). Returns whether it was marked.
  Future<bool> markNoteSyncedIfUnchanged(
      String id, DateTime seenUpdatedAt) async {
    final changed = await _db.rawUpdate(
      "UPDATE notes SET sync_status = 'synced', "
      'title_synced_rev = title_rev, text_synced_rev = text_rev, '
      'ink_synced_rev = ink_rev WHERE id = ? AND updated_at = ?',
      [id, seenUpdatedAt.toIso8601String()],
    );
    return changed > 0;
  }

  /// Re-keys local notebook [localId] to [remote], the copy the server just
  /// created for it, and moves its notes and child notebooks along, so the
  /// local store refers to the notebook by the id the server knows.
  Future<void> adoptRemoteNotebook(String localId, Notebook remote) async {
    await _db.transaction((txn) async {
      await txn.insert('notebooks', remote.toMap(),
          conflictAlgorithm: ConflictAlgorithm.replace);
      await txn.update('notes', {'notebook_id': remote.id},
          where: 'notebook_id = ?', whereArgs: [localId]);
      await txn.update('notebooks', {'parent_id': remote.id},
          where: 'parent_id = ?', whereArgs: [localId]);
      await txn.delete('notebooks', where: 'id = ?', whereArgs: [localId]);
    });
  }

  /// Inserts [notebook] or replaces an existing row with the same id.
  Future<void> upsertNotebook(Notebook notebook) async {
    await _db.insert(
      'notebooks',
      notebook.toMap(),
      conflictAlgorithm: ConflictAlgorithm.replace,
    );
  }

  /// Removes the row with [id] from `notes`. Hard delete — used by sync to
  /// drop notes that were 'synced' locally but no longer exist on the
  /// remote (the user deleted them server-side). Local-only and modified
  /// notes are skipped by callers, so they survive a pull.
  Future<int> hardDeleteNote(String id) async {
    return _db.delete('notes', where: 'id = ?', whereArgs: [id]);
  }

  /// Removes the row with [id] from `notebooks` (hard delete).
  Future<int> hardDeleteNotebook(String id) async {
    return _db.delete('notebooks', where: 'id = ?', whereArgs: [id]);
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

  /// Replaces the entire stroke set for [noteId] in a single transaction.
  ///
  /// The ink editor hands us the full list of completed strokes every time one
  /// finishes, so the simplest correct persistence is to swap the note's
  /// strokes wholesale: delete the old strokes (and their points) and insert
  /// the new ones. Running inside a transaction means a mid-write failure never
  /// leaves the note with a half-saved drawing. Stroke order is preserved by
  /// the caller assigning monotonically increasing `created_at` values, which
  /// [getStrokesForNote] orders by.
  Future<void> replaceStrokesForNote(
    String noteId,
    List<Stroke> strokes,
  ) async {
    await _db.transaction((txn) => _replaceStrokes(txn, noteId, strokes));
  }

  /// Stroke ids are a global primary key. A stroke whose id another note
  /// already uses (the same drawing in two notes) is stored under an id of
  /// its own for this note, never moved away from the other one.
  static Future<void> _replaceStrokes(
    DatabaseExecutor txn,
    String noteId,
    List<Stroke> strokes,
  ) async {
    final existing = await txn.query(
      'strokes',
      columns: ['id'],
      where: 'note_id = ?',
      whereArgs: [noteId],
    );
    for (final row in existing) {
      await txn.delete(
        'stroke_points',
        where: 'stroke_id = ?',
        whereArgs: [row['id']],
      );
    }
    await txn.delete('strokes', where: 'note_id = ?', whereArgs: [noteId]);

    final taken = <String>{};
    final ids = strokes.map((s) => s.id).toList();
    for (var i = 0; i < ids.length; i += 500) {
      final chunk = ids.sublist(i, i + 500 > ids.length ? ids.length : i + 500);
      final rows = await txn.query('strokes',
          columns: ['id'],
          where: 'id IN (${List.filled(chunk.length, '?').join(', ')})',
          whereArgs: chunk);
      taken.addAll(rows.map((r) => r['id'] as String));
    }
    for (final stroke in strokes) {
      final id = taken.contains(stroke.id) ? '${stroke.id}@$noteId' : stroke.id;
      await txn.insert(
        'strokes',
        {...stroke.toMap(), 'id': id, 'note_id': noteId},
        conflictAlgorithm: ConflictAlgorithm.replace,
      );
      await txn.delete('stroke_points', where: 'stroke_id = ?', whereArgs: [id]);
      for (var i = 0; i < stroke.points.length; i++) {
        await txn.insert('stroke_points', stroke.points[i].toMap(id, i));
      }
    }
  }

  /// Returns all strokes for [noteId] with their points, ordered by creation.
  Future<List<Stroke>> getStrokesForNote(String noteId) =>
      _strokesForNote(_db, noteId);

  static Future<List<Stroke>> _strokesForNote(
      DatabaseExecutor db, String noteId) async {
    final strokeRows = await db.query(
      'strokes',
      where: 'note_id = ?',
      whereArgs: [noteId],
      orderBy: 'created_at ASC',
    );

    final strokes = <Stroke>[];
    for (final strokeRow in strokeRows) {
      final strokeId = strokeRow['id'] as String;
      final pointRows = await db.query(
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
