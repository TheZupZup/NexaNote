import 'dart:convert';

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
      createdAt: createdAt,
      updatedAt: updatedAt ?? this.updatedAt,
    );
  }

  @override
  String toString() => 'Note(id: $id, title: $title, type: $noteType)';
}
