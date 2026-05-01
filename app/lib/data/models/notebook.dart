/// A notebook that groups related notes.
///
/// [parentId] enables nested notebooks (sub-folders).
/// [syncStatus] values: 'local_only' | 'synced' | 'modified' | 'conflict'
///
/// Mirrors Notebook in nexanote/models/note.py.
class Notebook {
  final String id;
  final String? parentId;
  final String name;
  final String description;
  final String color;
  final String icon;
  final bool isArchived;
  final String syncStatus;
  final DateTime createdAt;
  final DateTime updatedAt;

  const Notebook({
    required this.id,
    this.parentId,
    required this.name,
    this.description = '',
    this.color = '#6366f1',
    this.icon = 'notebook',
    this.isArchived = false,
    this.syncStatus = 'local_only',
    required this.createdAt,
    required this.updatedAt,
  });

  factory Notebook.fromMap(Map<String, dynamic> map) {
    return Notebook(
      id: map['id'] as String,
      parentId: map['parent_id'] as String?,
      name: (map['name'] as String?) ?? 'New Notebook',
      description: (map['description'] as String?) ?? '',
      color: (map['color'] as String?) ?? '#6366f1',
      icon: (map['icon'] as String?) ?? 'notebook',
      isArchived: ((map['is_archived'] as int?) ?? 0) == 1,
      syncStatus: (map['sync_status'] as String?) ?? 'local_only',
      createdAt: DateTime.parse(map['created_at'] as String),
      updatedAt: DateTime.parse(map['updated_at'] as String),
    );
  }

  Map<String, dynamic> toMap() {
    return {
      'id': id,
      'parent_id': parentId,
      'name': name,
      'description': description,
      'color': color,
      'icon': icon,
      'is_archived': isArchived ? 1 : 0,
      'sync_status': syncStatus,
      'created_at': createdAt.toIso8601String(),
      'updated_at': updatedAt.toIso8601String(),
    };
  }

  Notebook copyWith({
    String? name,
    String? description,
    String? color,
    String? icon,
    bool? isArchived,
    String? syncStatus,
    DateTime? updatedAt,
  }) {
    return Notebook(
      id: id,
      parentId: parentId,
      name: name ?? this.name,
      description: description ?? this.description,
      color: color ?? this.color,
      icon: icon ?? this.icon,
      isArchived: isArchived ?? this.isArchived,
      syncStatus: syncStatus ?? this.syncStatus,
      createdAt: createdAt,
      updatedAt: updatedAt ?? this.updatedAt,
    );
  }

  @override
  String toString() => 'Notebook(id: $id, name: $name)';
}
