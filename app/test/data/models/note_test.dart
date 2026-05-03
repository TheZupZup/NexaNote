import 'dart:convert';

import 'package:flutter_test/flutter_test.dart';

import 'package:nexanote/data/models/note.dart';

void main() {
  final _ts = DateTime.utc(2024, 6, 1, 9, 0);

  group('Note', () {
    test('toMap / fromMap round-trip preserves all fields', () {
      final note = Note(
        id: 'note-abc',
        notebookId: 'nb-1',
        title: 'My Note',
        noteType: 'handwritten',
        tags: ['flutter', 'android'],
        typedContent: '# Hello',
        isPinned: true,
        isArchived: false,
        isDeleted: false,
        syncStatus: 'modified',
        remoteId: 'md.SGVsbG8',
        remotePath: 'notes/Hello.md',
        createdAt: _ts,
        updatedAt: _ts,
      );

      final restored = Note.fromMap(note.toMap());

      expect(restored.id, note.id);
      expect(restored.notebookId, note.notebookId);
      expect(restored.title, note.title);
      expect(restored.noteType, note.noteType);
      expect(restored.tags, note.tags);
      expect(restored.typedContent, note.typedContent);
      expect(restored.isPinned, note.isPinned);
      expect(restored.isArchived, note.isArchived);
      expect(restored.isDeleted, note.isDeleted);
      expect(restored.syncStatus, note.syncStatus);
      expect(restored.remoteId, note.remoteId);
      expect(restored.remotePath, note.remotePath);
      expect(restored.createdAt, note.createdAt);
      expect(restored.updatedAt, note.updatedAt);
    });

    test('remoteId and remotePath default to null', () {
      final note = Note(
        id: 'note-default',
        title: 'Default',
        createdAt: _ts,
        updatedAt: _ts,
      );
      expect(note.remoteId, isNull);
      expect(note.remotePath, isNull);
      final restored = Note.fromMap(note.toMap());
      expect(restored.remoteId, isNull);
      expect(restored.remotePath, isNull);
    });

    test('copyWith updates remoteId and remotePath', () {
      final note = Note(
        id: 'n',
        title: 'T',
        createdAt: _ts,
        updatedAt: _ts,
      );
      final updated = note.copyWith(
        remoteId: 'md.AAAA',
        remotePath: 'notes/Foo.md',
      );
      expect(updated.remoteId, 'md.AAAA');
      expect(updated.remotePath, 'notes/Foo.md');
    });

    test('tags are serialized as JSON array string', () {
      final note = Note(
        id: 'note-1',
        title: 'Tagged',
        tags: ['a', 'b', 'c'],
        createdAt: _ts,
        updatedAt: _ts,
      );
      final map = note.toMap();
      expect(map['tags'], isA<String>());
      final decoded = jsonDecode(map['tags'] as String) as List;
      expect(decoded, ['a', 'b', 'c']);
    });

    test('empty tags round-trip correctly', () {
      final note = Note(
        id: 'note-2',
        title: 'No Tags',
        tags: [],
        createdAt: _ts,
        updatedAt: _ts,
      );
      final restored = Note.fromMap(note.toMap());
      expect(restored.tags, isEmpty);
    });

    test('boolean flags encode to 0/1 integers', () {
      final note = Note(
        id: 'note-3',
        title: 'Flags',
        isPinned: true,
        isArchived: false,
        isDeleted: true,
        createdAt: _ts,
        updatedAt: _ts,
      );
      final map = note.toMap();
      expect(map['is_pinned'], 1);
      expect(map['is_archived'], 0);
      expect(map['is_deleted'], 1);
    });

    test('copyWith changes only specified fields', () {
      final note = Note(
        id: 'note-4',
        notebookId: 'nb-1',
        title: 'Original',
        createdAt: _ts,
        updatedAt: _ts,
      );
      final updated = note.copyWith(
        title: 'Updated',
        syncStatus: 'modified',
        updatedAt: _ts.add(const Duration(minutes: 5)),
      );

      expect(updated.id, 'note-4');
      expect(updated.notebookId, 'nb-1');
      expect(updated.title, 'Updated');
      expect(updated.syncStatus, 'modified');
      expect(updated.createdAt, _ts);
    });

    test('null notebookId survives round-trip', () {
      final note = Note(
        id: 'note-5',
        title: 'Orphan',
        createdAt: _ts,
        updatedAt: _ts,
      );
      final restored = Note.fromMap(note.toMap());
      expect(restored.notebookId, isNull);
    });
  });
}
