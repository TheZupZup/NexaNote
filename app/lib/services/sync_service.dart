import '../data/models/note.dart';
import '../data/models/notebook.dart';
import 'api_client.dart' as api;
import 'local_note_service.dart';

/// Outcome of a single [SyncService.sync] call.
class SyncResult {
  final int notebooksPushed;
  final int notesPushed;
  final int notebooksPulled;
  final int notesPulled;

  const SyncResult({
    required this.notebooksPushed,
    required this.notesPushed,
    required this.notebooksPulled,
    required this.notesPulled,
  });

  String get summary =>
      'pushed $notebooksPushed nb / $notesPushed notes, '
      'pulled $notebooksPulled nb / $notesPulled notes';
}

/// Manual sync between the local SQLite store and the Python backend.
///
/// Phase 4A keeps this deliberately blunt: push everything local to the
/// backend, then replace the local DB with whatever the backend reports.
/// No incremental sync, no timestamp diffing, no conflict resolution —
/// that work belongs to a later phase.
class SyncService {
  final api.ApiClient _api;
  final LocalNoteService _local;

  SyncService({required api.ApiClient apiClient, required LocalNoteService local})
      : _api = apiClient,
        _local = local;

  /// Push local state, then pull-and-replace from the backend.
  Future<SyncResult> sync() async {
    final pushed = await pushLocal();
    final pulled = await pullRemote();
    return SyncResult(
      notebooksPushed: pushed.notebooks,
      notesPushed: pushed.notes,
      notebooksPulled: pulled.notebooks,
      notesPulled: pulled.notes,
    );
  }

  /// Uploads every local notebook and note to the backend via the existing
  /// REST endpoints. The backend assigns its own IDs; the subsequent pull
  /// brings those server-side records back into the local store.
  Future<_Counts> pushLocal() async {
    final snapshot = await _local.exportAllData();
    var notebooks = 0;
    var notes = 0;
    for (final nb in snapshot.notebooks) {
      await _api.createNotebook(name: nb.name, color: nb.color);
      notebooks++;
    }
    for (final note in snapshot.notes) {
      await _api.createNote(
        title: note.title,
        noteType: note.noteType,
        notebookId: note.notebookId,
      );
      notes++;
    }
    return _Counts(notebooks: notebooks, notes: notes);
  }

  /// Fetches the backend's notebooks and notes and overwrites the local DB.
  Future<_Counts> pullRemote() async {
    final remoteNotebooks = await _api.getNotebooks();
    final remoteNotes = await _api.getNotes(includeDeleted: true);

    final notebooks = remoteNotebooks.map(_toLocalNotebook).toList();
    final notes = remoteNotes.map(_toLocalNote).toList();

    await _local.importAllData(notebooks: notebooks, notes: notes);
    return _Counts(notebooks: notebooks.length, notes: notes.length);
  }

  Notebook _toLocalNotebook(api.Notebook nb) {
    final updated = _parseUtc(nb.updatedAt);
    return Notebook(
      id: nb.id,
      name: nb.name,
      color: nb.color,
      icon: nb.icon,
      syncStatus: 'synced',
      createdAt: updated,
      updatedAt: updated,
    );
  }

  Note _toLocalNote(api.Note n) {
    final updated = _parseUtc(n.updatedAt);
    final created = _parseUtc(n.createdAt, fallback: updated);
    return Note(
      id: n.id,
      notebookId: n.notebookId,
      title: n.title,
      noteType: n.noteType,
      tags: n.tags,
      isPinned: n.isPinned,
      isDeleted: n.isDeleted,
      syncStatus: 'synced',
      createdAt: created,
      updatedAt: updated,
    );
  }

  static DateTime _parseUtc(String iso, {DateTime? fallback}) {
    if (iso.isEmpty) return fallback ?? DateTime.now().toUtc();
    return DateTime.tryParse(iso)?.toUtc() ?? fallback ?? DateTime.now().toUtc();
  }
}

class _Counts {
  final int notebooks;
  final int notes;
  const _Counts({required this.notebooks, required this.notes});
}
