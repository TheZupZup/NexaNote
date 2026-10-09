import 'dart:convert';

/// The independently saved parts of a note, each with its own edit
/// revision (see [Note.titleRev] and friends).
enum NotePart {
  title('title_rev', 'title_synced_rev'),
  text('text_rev', 'text_synced_rev'),
  ink('ink_rev', 'ink_synced_rev');

  const NotePart(this.revColumn, this.syncedRevColumn);
  final String revColumn;
  final String syncedRevColumn;
}

/// A note in a notebook.
///
/// For Phase 2 this model covers the notes table directly.
/// [typedContent] holds the Markdown text for the first (and currently only)
/// page — matching how the existing Flutter editor uses the backend today.
///
/// [syncStatus] values: 'local_only' | 'synced' | 'modified' | 'conflict'
/// [noteType]   values: 'typed' | 'handwritten' | 'mixed'
///
/// [remoteId] is the stable identifier the WebDAV/NAS source of truth uses
/// for this note (the frontmatter `id` for notes with NexaNote frontmatter,
/// or the synthetic `md.<base64>` id for plain Markdown files dropped in by
/// the user). When set it lets the sync engine adopt the remote .md file
/// instead of creating a duplicate.
///
/// [remotePath] is the relative path of the canonical .md file on the
/// remote (e.g. `notes/Hello World.md`). Stored so renames on disk can be
/// followed without losing the link.
///
/// [remoteBaseline] is the server's `updated_at` for the note right after
/// our push created it or last wrote content into it. While an upload is
/// pending it is the only proof that nobody else changed the remote note
/// since.
///
/// The `*Rev` / `*SyncedRev` pairs count local edits of the title, the text
/// and the drawing, and the last of each the server has confirmed. A part
/// whose revision is ahead is a local edit not yet on the server; the local
/// row is its only durable copy until then.
///
/// [hasContent] is false for rows that only hold a note's metadata (what a
/// pull brings): their text and drawing were never downloaded, so they
/// are not the note's content. [blindEdit] marks text or ink edited into
/// such a row in local mode, on what looked like an empty page: sending
/// that into the server note would wipe its real content, so it becomes a
/// note of its own instead.
///
/// Mirrors Note in nexanote/models/note.py.
class Note {
  final String id;
  final String? notebookId;
  final String title;
  final String noteType;
  final List<String> tags;
  final String typedContent;
  final bool isPinned;
  final bool isArchived;
  final bool isDeleted;
  final String syncStatus;
  final String? remoteId;
  final String? remotePath;
  final String? remoteBaseline;
  final int titleRev;
  final int titleSyncedRev;
  final int textRev;
  final int textSyncedRev;
  final int inkRev;
  final int inkSyncedRev;
  final bool hasContent;
  final bool blindEdit;
  final DateTime createdAt;
  final DateTime updatedAt;

  const Note({
    required this.id,
    this.notebookId,
    required this.title,
    this.noteType = 'typed',
    this.tags = const [],
    this.typedContent = '',
    this.isPinned = false,
    this.isArchived = false,
    this.isDeleted = false,
    this.syncStatus = 'local_only',
    this.remoteId,
    this.remotePath,
    this.remoteBaseline,
    this.titleRev = 0,
    this.titleSyncedRev = 0,
    this.textRev = 0,
    this.textSyncedRev = 0,
    this.inkRev = 0,
    this.inkSyncedRev = 0,
    this.hasContent = true,
    this.blindEdit = false,
    required this.createdAt,
    required this.updatedAt,
  });

  factory Note.fromMap(Map<String, dynamic> map) {
    final tagsJson = (map['tags'] as String?) ?? '[]';
    final List<dynamic> tagsList = jsonDecode(tagsJson);
    return Note(
      id: map['id'] as String,
      notebookId: map['notebook_id'] as String?,
      title: (map['title'] as String?) ?? 'Untitled',
      noteType: (map['note_type'] as String?) ?? 'typed',
      tags: tagsList.cast<String>(),
      typedContent: (map['typed_content'] as String?) ?? '',
      isPinned: ((map['is_pinned'] as int?) ?? 0) == 1,
      isArchived: ((map['is_archived'] as int?) ?? 0) == 1,
      isDeleted: ((map['is_deleted'] as int?) ?? 0) == 1,
      syncStatus: (map['sync_status'] as String?) ?? 'local_only',
      remoteId: map['remote_id'] as String?,
      remotePath: map['remote_path'] as String?,
      remoteBaseline: map['remote_baseline'] as String?,
      titleRev: (map['title_rev'] as int?) ?? 0,
      titleSyncedRev: (map['title_synced_rev'] as int?) ?? 0,
      textRev: (map['text_rev'] as int?) ?? 0,
      textSyncedRev: (map['text_synced_rev'] as int?) ?? 0,
      inkRev: (map['ink_rev'] as int?) ?? 0,
      inkSyncedRev: (map['ink_synced_rev'] as int?) ?? 0,
      hasContent: ((map['has_content'] as int?) ?? 1) == 1,
      blindEdit: ((map['blind_edit'] as int?) ?? 0) == 1,
      createdAt: DateTime.parse(map['created_at'] as String),
      updatedAt: DateTime.parse(map['updated_at'] as String),
    );
  }

  Map<String, dynamic> toMap() {
    return {
      'id': id,
      'notebook_id': notebookId,
      'title': title,
      'note_type': noteType,
      'tags': jsonEncode(tags),
      'typed_content': typedContent,
      'is_pinned': isPinned ? 1 : 0,
      'is_archived': isArchived ? 1 : 0,
      'is_deleted': isDeleted ? 1 : 0,
      'sync_status': syncStatus,
      'remote_id': remoteId,
      'remote_path': remotePath,
      'remote_baseline': remoteBaseline,
      'title_rev': titleRev,
      'title_synced_rev': titleSyncedRev,
      'text_rev': textRev,
      'text_synced_rev': textSyncedRev,
      'ink_rev': inkRev,
      'ink_synced_rev': inkSyncedRev,
      'has_content': hasContent ? 1 : 0,
      'blind_edit': blindEdit ? 1 : 0,
      'created_at': createdAt.toIso8601String(),
      'updated_at': updatedAt.toIso8601String(),
    };
  }

  Note copyWith({
    String? notebookId,
    bool clearNotebookId = false,
    String? title,
    String? noteType,
    List<String>? tags,
    String? typedContent,
    bool? isPinned,
    bool? isArchived,
    bool? isDeleted,
    String? syncStatus,
    String? remoteId,
    String? remotePath,
    bool? hasContent,
    DateTime? updatedAt,
  }) {
    return Note(
      id: id,
      notebookId:
          clearNotebookId ? null : (notebookId ?? this.notebookId),
      title: title ?? this.title,
      noteType: noteType ?? this.noteType,
      tags: tags ?? this.tags,
      typedContent: typedContent ?? this.typedContent,
      isPinned: isPinned ?? this.isPinned,
      isArchived: isArchived ?? this.isArchived,
      isDeleted: isDeleted ?? this.isDeleted,
      syncStatus: syncStatus ?? this.syncStatus,
      remoteId: remoteId ?? this.remoteId,
      remotePath: remotePath ?? this.remotePath,
      remoteBaseline: remoteBaseline,
      titleRev: titleRev,
      titleSyncedRev: titleSyncedRev,
      textRev: textRev,
      textSyncedRev: textSyncedRev,
      inkRev: inkRev,
      inkSyncedRev: inkSyncedRev,
      hasContent: hasContent ?? this.hasContent,
      blindEdit: blindEdit,
      createdAt: createdAt,
      updatedAt: updatedAt ?? this.updatedAt,
    );
  }

  bool get titlePending => titleRev > titleSyncedRev;
  bool get textPending => textRev > textSyncedRev;
  bool get inkPending => inkRev > inkSyncedRev;
  bool get hasPendingEdits => titlePending || textPending || inkPending;

  @override
  String toString() => 'Note(id: $id, title: $title, type: $noteType)';
}
