import 'package:sqflite/sqflite.dart';

import '../data/database/app_database.dart';
import '../data/models/note.dart';
import '../data/models/notebook.dart';
import '../data/models/stroke.dart';
import '../data/repositories/note_repository.dart';

/// Bundle of all local notebooks and notes — the unit moved by SyncService.
class LocalSnapshot {
  final List<Notebook> notebooks;
  final List<Note> notes;

  const LocalSnapshot({required this.notebooks, required this.notes});
}

/// Owns local SQLite initialization and exposes a small, AppState-friendly
/// surface over [NoteRepository].
///
/// LocalNoteService is the *only* thing that talks to sqflite directly.
/// AppState calls into this service; the service owns the [Database] and the
/// [NoteRepository] wiring.
///
/// Tests inject an in-memory database via the [database] parameter; in that
/// case [close] is a no-op because the service does not own the connection.
class LocalNoteService {
  final Database? _injectedDb;
  Database? _db;
  NoteRepository? _repository;

  LocalNoteService({Database? database}) : _injectedDb = database;

  bool get isInitialized => _repository != null;

  /// Opens the database (idempotent) and prepares the repository.
  Future<void> initialize() async {
    if (_repository != null) return;
    _db = _injectedDb ?? await AppDatabase.open();
    _repository = NoteRepository(_db!);
  }

  // Notebooks
  Future<List<Notebook>> getNotebooks() => _repo.getNotebooks();
  Future<Notebook> createNotebook(String name, {String color = '#6366f1'}) =>
      _repo.createNotebook(name, color: color);

  // Notes
  Future<List<Note>> getNotesForNotebook(String notebookId) =>
      _repo.getNotesForNotebook(notebookId);
  Future<Note> createNote(
    String title, {
    String? notebookId,
    String noteType = 'typed',
  }) =>
      _repo.createNote(title, notebookId: notebookId, noteType: noteType);
  Future<Note?> getNoteById(String id) => _repo.getNoteById(id);
  Future<Note?> getNoteByRemoteId(String remoteId) =>
      _repo.getNoteByRemoteId(remoteId);
  Future<void> upsertNote(Note note) => _repo.upsertNote(note);

  /// Soft-deletes a note (sets `is_deleted`, marks it `modified`) so the
  /// deletion can later propagate once a backend is configured. Used by the
  /// local-mode delete path; sync uses [hardDeleteNote] for server-driven
  /// removals.
  Future<void> deleteNote(String id) => _repo.deleteNote(id);
  Future<void> upsertNotebook(Notebook notebook) =>
      _repo.upsertNotebook(notebook);
  Future<int> hardDeleteNote(String id) => _repo.hardDeleteNote(id);
  Future<int> hardDeleteNotebook(String id) =>
      _repo.hardDeleteNotebook(id);

  // Strokes
  Future<void> saveStroke(Stroke stroke) => _repo.saveStroke(stroke);
  Future<List<Stroke>> getStrokesForNote(String noteId) =>
      _repo.getStrokesForNote(noteId);

  /// Snapshot of all locally-stored notebooks and notes.
  ///
  /// Strokes are intentionally excluded — sync covers metadata only.
  /// Used by SyncService to enumerate the local state both for the push
  /// half of a cycle and for the merge step of a pull.
  Future<LocalSnapshot> exportAllData() async {
    final notebooks = await _repo.getNotebooks(includeArchived: true);
    final notes = await _repo.getAllNotes(includeDeleted: true);
    return LocalSnapshot(notebooks: notebooks, notes: notes);
  }

  /// Closes the database opened by this service. No-op when an injected
  /// [database] was supplied — the caller owns that connection.
  Future<void> close() async {
    if (_injectedDb == null) await _db?.close();
    _db = null;
    _repository = null;
  }

  NoteRepository get _repo {
    final r = _repository;
    if (r == null) {
      throw StateError(
        'LocalNoteService.initialize() must be called before use',
      );
    }
    return r;
  }
}
