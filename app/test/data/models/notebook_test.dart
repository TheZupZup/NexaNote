import 'package:flutter_test/flutter_test.dart';

import 'package:nexanote/data/models/notebook.dart';

void main() {
  final _ts = DateTime.utc(2024, 6, 1, 9, 0);

  group('Notebook', () {
    test('toMap / fromMap round-trip preserves all fields', () {
      final nb = Notebook(
        id: 'nb-abc',
        parentId: 'nb-parent',
        name: 'Work',
        description: 'Work notes',
        color: '#ff6600',
        icon: 'briefcase',
        isArchived: false,
        syncStatus: 'synced',
        createdAt: _ts,
        updatedAt: _ts,
      );

      final restored = Notebook.fromMap(nb.toMap());

      expect(restored.id, nb.id);
      expect(restored.parentId, nb.parentId);
      expect(restored.name, nb.name);
      expect(restored.description, nb.description);
      expect(restored.color, nb.color);
      expect(restored.icon, nb.icon);
      expect(restored.isArchived, nb.isArchived);
      expect(restored.syncStatus, nb.syncStatus);
      expect(restored.createdAt, nb.createdAt);
      expect(restored.updatedAt, nb.updatedAt);
    });

    test('fromMap uses defaults for missing optional fields', () {
      final map = <String, dynamic>{
        'id': 'nb-1',
        'parent_id': null,
        'name': 'Quick',
        'description': '',
        'color': '#6366f1',
        'icon': 'notebook',
        'is_archived': 0,
        'sync_status': 'local_only',
        'created_at': _ts.toIso8601String(),
        'updated_at': _ts.toIso8601String(),
      };
      final nb = Notebook.fromMap(map);
      expect(nb.parentId, isNull);
      expect(nb.syncStatus, 'local_only');
    });

    test('copyWith changes only specified fields', () {
      final nb = Notebook(
        id: 'nb-1',
        name: 'Old',
        createdAt: _ts,
        updatedAt: _ts,
      );
      final updated = nb.copyWith(
        name: 'New',
        syncStatus: 'modified',
        updatedAt: _ts.add(const Duration(hours: 1)),
      );

      expect(updated.id, 'nb-1');
      expect(updated.name, 'New');
      expect(updated.syncStatus, 'modified');
      expect(updated.createdAt, _ts);
      expect(updated.updatedAt, isNot(_ts));
    });

    test('toMap encodes isArchived as 0/1 integer', () {
      final nb = Notebook(
        id: 'nb-2',
        name: 'X',
        isArchived: true,
        createdAt: _ts,
        updatedAt: _ts,
      );
      expect(nb.toMap()['is_archived'], 1);
    });
  });
}
