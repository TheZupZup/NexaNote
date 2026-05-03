import 'dart:convert';

import '../data/models/note.dart';
import '../data/models/notebook.dart';
import 'api_client.dart' as api;
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

  /// Uploads only locally-originated records that have never been seen on
  /// the server. A row is considered "needs push" iff it has no
  /// [Note.remoteId] AND its `syncStatus` is not `synced`. After a pull,
  /// every adopted note carries `synced` plus a `remoteId`, so subsequent
  /// pushes are no-ops until the user creates something new.
  Future<_PushCounts> pushLocal() async {
    final snapshot = await _local.exportAllData();
    var notebooks = 0;
    var notes = 0;
    for (final nb in snapshot.notebooks) {
      if (_isAlreadySynced(nb.syncStatus)) continue;
      await _api.createNotebook(name: nb.name, color: nb.color);
      notebooks++;
    }
    for (final note in snapshot.notes) {
      if (_isAlreadySynced(note.syncStatus)) continue;
      // Already adopted from a remote .md file — never re-create on the
      // server. The non-idempotent createNote endpoint would mint a fresh
      // UUID and a duplicate file alongside the existing one.
      if (note.remoteId != null && note.remoteId!.isNotEmpty) continue;
      await _api.createNote(
        title: cleanRemoteTitle(note.title),
        noteType: note.noteType,
        notebookId: note.notebookId,
      );
      notes++;
    }
    return _PushCounts(notebooks: notebooks, notes: notes);
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
        final keepLocal = existing.syncStatus == 'modified' &&
            existing.updatedAt.isAfter(remoteUpdated);
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
