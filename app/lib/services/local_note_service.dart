import 'package:sqflite/sqflite.dart';

import '../data/database/app_database.dart';
import '../data/models/note.dart';
import '../data/models/notebook.dart';
import '../data/models/stroke.dart';
import '../data/repositories/note_repository.dart';

/// Owns local SQLite initialization and exposes a small, AppState-friendly
/// surface over [NoteRepository].
///
/// The service is the single entry point AppState uses for local persistence:
/// it hides the [Database] handle and the repository wiring so AppState never
/// has to talk to sqflite directly.
///
/// Tests can either inject a custom [databaseOpener] (e.g. one that opens an
/// in-memory database via sqflite_common_ffi) or build the service from a
/// pre-constructed [NoteRepository] using [LocalNoteService.withRepository].
class LocalNoteService {
  final Future<Database> Function() _databaseOpener;
  Database? _db;
  NoteRepository? _repository;

  LocalNoteService({Future<Database> Function()? databaseOpener})
      : _databaseOpener = databaseOpener ?? (() => AppDatabase.open());

  /// Test seam: skip database opening with a pre-built repository.
  /// [close] becomes a no-op since this service does not own the database.
  LocalNoteService.withRepository(NoteRepository repository)
      : _repository = repository,
        _databaseOpener = (() async {
          throw StateError('LocalNoteService.withRepository: already wired');
        });

  bool get isInitialized => _repository != null;

  /// Opens the database (idempotent) and prepares the repository.
  Future<void> initialize() async {
    if (_repository != null) return;
    _db = await _databaseOpener();
    _repository = NoteRepository(_db!);
  }

  // ---------------------------------------------------------------------
  // Notebooks
  // ---------------------------------------------------------------------

  Future<List<Notebook>> getNotebooks() => _repo.getNotebooks();

  Future<Notebook> createNotebook(String name, {String color = '#6366f1'}) =>
      _repo.createNotebook(name, color: color);

  // ---------------------------------------------------------------------
  // Notes
  // ---------------------------------------------------------------------

  Future<List<Note>> getNotesForNotebook(String notebookId) =>
      _repo.getNotesForNotebook(notebookId);

  Future<Note> createNote(
    String title, {
    String? notebookId,
    String noteType = 'typed',
  }) =>
      _repo.createNote(title, notebookId: notebookId, noteType: noteType);

  Future<Note?> getNoteById(String id) => _repo.getNoteById(id);

  // ---------------------------------------------------------------------
  // Strokes
  // ---------------------------------------------------------------------

  Future<void> saveStroke(Stroke stroke) => _repo.saveStroke(stroke);

  Future<List<Stroke>> getStrokesForNote(String noteId) =>
      _repo.getStrokesForNote(noteId);

  // ---------------------------------------------------------------------
  // Shutdown
  // ---------------------------------------------------------------------

  /// Closes the database opened by this service. No-op when constructed
  /// via [LocalNoteService.withRepository].
  Future<void> close() async {
    await _db?.close();
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
