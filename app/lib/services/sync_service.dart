import 'dart:convert';

import '../data/models/note.dart';
import '../data/models/notebook.dart';
import 'api_client.dart' as api;
import 'ink_json.dart';
import 'local_note_service.dart';
import 'title_cleaner.dart' as title_cleaner;

/// Outcome of a single [SyncService.sync] call.
class SyncResult {
  final int notebooksPushed;
  final int notesPushed;
  final int notebooksPulled;
  final int notesPulled;
  final int notesAdopted;

  const SyncResult({
    required this.notebooksPushed,
    required this.notesPushed,
    required this.notebooksPulled,
    required this.notesPulled,
    this.notesAdopted = 0,
  });

  String get summary =>
      'pushed $notebooksPushed nb / $notesPushed notes, '
      'pulled $notebooksPulled nb / $notesPulled notes '
      '(adopted $notesAdopted)';
}

/// Sync between the local SQLite store and the WebDAV/NAS-backed Python
/// backend that exposes `.md` files as the canonical note representation.
///
/// **Pull is adopt-not-replace.** Existing remote `.md` files become local
/// notes: each is keyed by its server id (real UUID for NexaNote-frontmatter
/// notes, synthetic `md.<base64>` id for plain Markdown files), persisted
/// in the local row's [Note.remoteId]. Subsequent pulls find the existing
/// row and update in place — running sync three times after the first
/// import does not multiply the note count.
///
/// **Push only sends genuinely new local rows.** A note that already
/// carries a `remoteId` (i.e. has been pulled/adopted at least once) is
/// skipped to avoid creating a duplicate via the non-idempotent `POST
/// /notes` endpoint.
///
/// **Conflict policy.** When a remote note's id matches a local one we
/// keep the side with the newer `updatedAt` (remote wins ties). Local
/// `local_only` and `modified` notes are preserved across pulls so the
/// user does not lose work in flight. When a remote note arrives with
/// the same cleaned title but a different id than an existing local
/// row, both are kept and the local row is marked `conflict`.
class SyncService {
  final api.ApiClient _api;
  final LocalNoteService _local;

  SyncService({required api.ApiClient apiClient, required LocalNoteService local})
      : _api = apiClient,
        _local = local;

  /// Push local-only changes, then adopt the remote view.
  Future<SyncResult> sync() async {
    final pushed = await pushLocal();
    final pulled = await pullRemote();
    return SyncResult(
      notebooksPushed: pushed.notebooks,
      notesPushed: pushed.notes,
      notebooksPulled: pulled.notebooks,
      notesPulled: pulled.notes,
      notesAdopted: pulled.adopted,
    );
  }

  /// Uploads locally-originated records the server has never seen.
  ///
  /// Notebooks are created first and the local row is re-keyed to the id the
  /// server minted, so notes are filed under a notebook the server knows.
  ///
  /// A note is created, then its text and drawing are uploaded, then it is
  /// marked `synced` with the server id as its [Note.remoteId]. If an upload
  /// fails after the create, the note stays `local_only` with its remoteId
  /// set and the next push resumes the upload instead of creating it again.
  /// Adopted notes (remoteId set by a pull, status synced/modified/conflict)
  /// are never pushed: local rows of pulled notes hold no page content, so
  /// uploading them would blank the server copy.
  Future<_PushCounts> pushLocal() async {
    final snapshot = await _local.exportAllData();
    var notebooks = 0;
    var notes = 0;
    final serverNotebookIds = <String, String>{};
    for (final nb in snapshot.notebooks) {
      if (_isAlreadySynced(nb.syncStatus)) {
        serverNotebookIds[nb.id] = nb.id;
        continue;
      }
      final created = await _api.createNotebook(name: nb.name, color: nb.color);
      await _local.adoptRemoteNotebook(
        nb.id,
        nb.copyWith(syncStatus: 'synced').withId(created.id),
      );
      serverNotebookIds[nb.id] = created.id;
      notebooks++;
    }
    for (final note in snapshot.notes) {
      if (_isAlreadySynced(note.syncStatus)) continue;
      final pendingUpload = note.syncStatus == 'local_only';
      final remoteId = note.remoteId;
      final hasRemote = remoteId != null && remoteId.isNotEmpty;
      if (hasRemote && !pendingUpload) continue;
      var target = hasRemote ? remoteId : null;
      if (target != null) {
        // Resuming an upload interrupted after the create. Only write into
        // that remote note if it is provably still the empty one we created;
        // anything else (edited online, even emptied on purpose, or a state
        // we can't compare) keeps both versions.
        final server = await _remoteNoteOrNull(target);
        if (server == null) {
          target = null; // deleted on the server meanwhile: create it again
        } else if (!_isUntouchedSinceCreate(server, note.remoteBaseline)) {
          final copy = await _api.createNote(
            title: '${cleanRemoteTitle(note.title)} (offline copy)',
            noteType: note.noteType,
            notebookId: server.notebookId,
          );
          await _uploadContent(note, copy.id);
          await _local.markNoteSyncedIfUnchanged(note.id, note.updatedAt);
          notes++;
          continue;
        }
      }
      if (target == null) {
        // Deleted before it ever reached the server: nothing to propagate.
        if (note.isDeleted) continue;
        final notebookId = note.notebookId;
        final created = await _api.createNote(
          title: cleanRemoteTitle(note.title),
          noteType: note.noteType,
          // A notebook deleted locally is unknown to the server, which would
          // reject the note; file it unsorted rather than abort the push.
          notebookId: notebookId == null ? null : serverNotebookIds[notebookId],
        );
        target = created.id;
        await _local.setNoteRemoteId(note.id, target, created.updatedAt);
        notes++;
      }
      await _uploadContent(note, target);
      await _local.markNoteSyncedIfUnchanged(note.id, note.updatedAt);
    }
    return _PushCounts(notebooks: notebooks, notes: notes);
  }

  Future<void> _uploadContent(Note note, String remoteId) async {
    if (note.typedContent.isNotEmpty) {
      await _api.savePageText(remoteId, 1, note.typedContent);
    }
    final strokes = await _local.getStrokesForNote(note.id);
    if (strokes.isNotEmpty) {
      await _api.savePageInk(
          remoteId, 1, strokes.map(inkJsonFromStroke).toList());
    }
  }

  Future<api.Note?> _remoteNoteOrNull(String id) async {
    try {
      return await _api.getNote(id);
    } on api.ApiException catch (e) {
      if (e.statusCode == 404) return null;
      rethrow;
    }
  }

  /// True only when [server] is still exactly the note our push created:
  /// same updated_at as recorded at create time ([baseline]) and no content.
  /// Every server-side write moves updated_at, so an emptied note or a note
  /// edited and reverted does not pass. Without a comparable baseline there
  /// is no proof, so this is false.
  static bool _isUntouchedSinceCreate(api.Note server, String? baseline) {
    final created = baseline == null ? null : DateTime.tryParse(baseline);
    final current = DateTime.tryParse(server.updatedAt);
    if (created == null || current == null) return false;
    if (!current.isAtSameMomentAs(created)) return false;
    return !(server.pages ?? const []).any(
        (p) => p.typedContent.isNotEmpty || p.strokes.isNotEmpty);
  }

  static bool _isAlreadySynced(String status) => status == 'synced';

  /// Adopts the remote view of notebooks and notes into the local DB. The
  /// merge is idempotent: running it back to back yields the same row set.
  ///
  /// Behaviour summary:
  /// - Each remote note is keyed by `id` (or `remote_id`) and upserted in
  ///   place; a brand-new row is inserted only when no local match exists.
  /// - Plain `.md` files (synthetic `md.<base64>` id) are adopted with the
  ///   server id stored as [Note.remoteId] so the next pull finds the
  ///   existing row instead of creating a duplicate.
  /// - Local-only and locally-modified notes are preserved.
  /// - Synced local notes that disappear from the remote are removed
  ///   (server-side delete propagates).
  /// - When a remote note carries the same cleaned title as an unrelated
  ///   local row (different id), both are kept and the local row is
  ///   flagged with `sync_status='conflict'`.
  Future<_PullCounts> pullRemote() async {
    final remoteNotebooks = await _api.getNotebooks();
    final remoteNotes = await _api.getNotes(includeDeleted: true);

    final localSnapshot = await _local.exportAllData();
    var adopted = 0;

    // ---------------- Notebooks ----------------
    final remoteNbIds = <String>{};
    for (final nb in remoteNotebooks) {
      remoteNbIds.add(nb.id);
      await _local.upsertNotebook(_toLocalNotebook(nb));
    }
    for (final localNb in localSnapshot.notebooks) {
      if (remoteNbIds.contains(localNb.id)) continue;
      // A notebook that disappeared on the server. Drop only if it was
      // already 'synced' — local-only notebooks survive.
      if (localNb.syncStatus == 'synced') {
        await _local.hardDeleteNotebook(localNb.id);
      }
    }

    // ---------------- Notes ----------------
    final localById = {for (final n in localSnapshot.notes) n.id: n};
    final localByRemoteId = <String, Note>{
      for (final n in localSnapshot.notes)
        if (n.remoteId != null && n.remoteId!.isNotEmpty) n.remoteId!: n
    };
    final localByRemotePath = <String, Note>{
      for (final n in localSnapshot.notes)
        if (n.remotePath != null && n.remotePath!.isNotEmpty)
          n.remotePath!: n
    };
    final localTitleIndex = <String, Note>{};
    for (final n in localSnapshot.notes) {
      final cleaned = cleanRemoteTitle(n.title).toLowerCase();
      localTitleIndex.putIfAbsent(cleaned, () => n);
    }

    final remoteIds = <String>{};
    final remotePaths = <String>{};
    for (final r in remoteNotes) {
      remoteIds.add(r.id);
      final cleanedRemoteTitle = cleanRemoteTitle(r.title);
      final derivedPath = _derivedRemotePath(r.id);
      if (derivedPath != null) remotePaths.add(derivedPath);

      final existing = localById[r.id] ??
          localByRemoteId[r.id] ??
          (derivedPath != null ? localByRemotePath[derivedPath] : null);
      if (existing != null) {
        // Same identity on both sides — newer timestamp wins, but a local
        // 'modified' row beats an older or equal-time remote so user edits
        // aren't clobbered before the next push round.
        final remoteUpdated = _parseUtc(r.updatedAt);
        // A local_only row with a remoteId was created by our own push and
        // still has content to upload; adopting the remote copy now would
        // mark it synced and the upload would never be retried.
        final keepLocal = existing.syncStatus == 'local_only' ||
            (existing.syncStatus == 'modified' &&
                existing.updatedAt.isAfter(remoteUpdated));
        if (keepLocal) continue;
        await _local.upsertNote(existing.copyWith(
          notebookId: r.notebookId,
          clearNotebookId: r.notebookId == null,
          title: cleanedRemoteTitle,
          noteType: r.noteType,
          tags: r.tags,
          isPinned: r.isPinned,
          isDeleted: r.isDeleted,
          syncStatus: 'synced',
          remoteId: r.id,
          remotePath: derivedPath ?? existing.remotePath,
          updatedAt: remoteUpdated,
        ));
        continue;
      }

      // No id match. Check whether the remote shares a cleaned title with
      // an unrelated local row — that's the "same title, different id"
      // case the conflict policy speaks to. Keep both, flag the local.
      final titleClash =
          localTitleIndex[cleanedRemoteTitle.toLowerCase()];
      if (titleClash != null && titleClash.remoteId == null) {
        await _local.upsertNote(titleClash.copyWith(
          syncStatus: 'conflict',
          updatedAt: titleClash.updatedAt,
        ));
      }

      await _local.upsertNote(_toLocalNote(
        r,
        cleanedTitle: cleanedRemoteTitle,
        derivedPath: derivedPath,
      ));
      adopted++;
    }
    for (final localNote in localSnapshot.notes) {
      if (remoteIds.contains(localNote.id)) continue;
      if (localNote.remoteId != null &&
          remoteIds.contains(localNote.remoteId)) {
        continue;
      }
      if (localNote.remotePath != null &&
          remotePaths.contains(localNote.remotePath)) {
        continue;
      }
      // Drop only fully-synced rows. Local-only / modified / conflict
      // rows are preserved; the next push promotes them upstream.
      if (localNote.syncStatus == 'synced') {
        await _local.hardDeleteNote(localNote.id);
      }
    }

    return _PullCounts(
      notebooks: remoteNotebooks.length,
      notes: remoteNotes.length,
      adopted: adopted,
    );
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

  Note _toLocalNote(
    api.Note n, {
    String? cleanedTitle,
    String? derivedPath,
  }) {
    final updated = _parseUtc(n.updatedAt);
    final created = _parseUtc(n.createdAt, fallback: updated);
    return Note(
      id: n.id,
      notebookId: n.notebookId,
      title: cleanedTitle ?? cleanRemoteTitle(n.title),
      noteType: n.noteType,
      tags: n.tags,
      isPinned: n.isPinned,
      isDeleted: n.isDeleted,
      syncStatus: 'synced',
      remoteId: n.id,
      remotePath: derivedPath ?? _derivedRemotePath(n.id),
      createdAt: created,
      updatedAt: updated,
    );
  }

  /// For plain Markdown remotes (synthetic id `md.<urlsafe-base64>` of the
  /// file stem), recover the canonical relative path so the note can be
  /// matched even if the server-issued id later changes. Returns null for
  /// real-UUID notes — the API doesn't surface their paths today.
  static String? _derivedRemotePath(String remoteId) {
    if (!remoteId.startsWith('md.')) return null;
    final encoded = remoteId.substring(3);
    if (encoded.isEmpty) return null;
    if (!_base64UrlSafeRe.hasMatch(encoded)) return null;
    try {
      final padded = encoded + '=' * ((4 - encoded.length % 4) % 4);
      final bytes = base64Url.decode(padded);
      final stem = utf8.decode(bytes);
      return 'notes/$stem.md';
    } catch (_) {
      return null;
    }
  }

  static final RegExp _base64UrlSafeRe = RegExp(r'^[A-Za-z0-9_-]+$');

  static DateTime _parseUtc(String iso, {DateTime? fallback}) {
    if (iso.isEmpty) return fallback ?? DateTime.now().toUtc();
    return DateTime.tryParse(iso)?.toUtc() ?? fallback ?? DateTime.now().toUtc();
  }

  /// Re-export of the shared title cleaner for tests and callers that
  /// already import [SyncService].
  static String cleanRemoteTitle(String raw) =>
      title_cleaner.cleanRemoteTitle(raw);
}

class _PushCounts {
  final int notebooks;
  final int notes;
  const _PushCounts({required this.notebooks, required this.notes});
}

class _PullCounts {
  final int notebooks;
  final int notes;
  final int adopted;
  const _PullCounts({
    required this.notebooks,
    required this.notes,
    required this.adopted,
  });
}
