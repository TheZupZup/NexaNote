import 'package:sqflite/sqflite.dart';

import '../data/database/app_database.dart';
import '../data/models/note.dart';
import '../data/models/notebook.dart';
import '../data/models/stroke.dart';
import '../data/repositories/note_repository.dart';

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

  // Strokes
  Future<void> saveStroke(Stroke stroke) => _repo.saveStroke(stroke);
  Future<List<Stroke>> getStrokesForNote(String noteId) =>
      _repo.getStrokesForNote(noteId);

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
