import 'package:flutter_test/flutter_test.dart';
import 'package:sqflite_common_ffi/sqflite_ffi.dart';

import 'package:nexanote/data/database/schema.dart';

void main() {
  setUpAll(() {
    sqfliteFfiInit();
    databaseFactory = databaseFactoryFfi;
  });

  group('Schema.onCreate', () {
    test('creates all expected tables', () async {
      final db = await openDatabase(
        inMemoryDatabasePath,
        version: Schema.version,
        onCreate: Schema.onCreate,
      );

      final tables = await db.rawQuery(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name",
      );
      final names = tables.map((r) => r['name'] as String).toList();

      expect(names, containsAll(['notebooks', 'notes', 'strokes', 'stroke_points']));
      await db.close();
    });

    test('creates expected indexes', () async {
      final db = await openDatabase(
        inMemoryDatabasePath,
        version: Schema.version,
        onCreate: Schema.onCreate,
      );

      final indexes = await db.rawQuery(
        "SELECT name FROM sqlite_master WHERE type='index' ORDER BY name",
      );
      final names = indexes.map((r) => r['name'] as String).toList();

      expect(names, containsAll([
        'idx_notes_notebook',
        'idx_notes_updated',
        'idx_strokes_note',
        'idx_stroke_points_stroke',
      ]));
      await db.close();
    });

    test('can insert and retrieve a row in each table', () async {
      final db = await openDatabase(
        inMemoryDatabasePath,
        version: Schema.version,
        onCreate: Schema.onCreate,
      );

      final now = DateTime.utc(2024, 1, 1).toIso8601String();

      await db.insert('notebooks', {
        'id': 'nb-1', 'name': 'Test', 'description': '', 'color': '#fff',
        'icon': 'notebook', 'is_archived': 0, 'sync_status': 'local_only',
        'created_at': now, 'updated_at': now,
      });

      await db.insert('notes', {
        'id': 'note-1', 'notebook_id': 'nb-1', 'title': 'T', 'note_type': 'typed',
        'tags': '[]', 'typed_content': '', 'is_pinned': 0,
        'is_archived': 0, 'is_deleted': 0, 'sync_status': 'local_only',
        'created_at': now, 'updated_at': now,
      });

      await db.insert('strokes', {
        'id': 'stroke-1', 'note_id': 'note-1',
        'color': '#000', 'width': 2.0, 'tool': 'pen', 'created_at': now,
      });

      await db.insert('stroke_points', {
        'stroke_id': 'stroke-1', 'x': 1.0, 'y': 2.0,
        'pressure': 0.5, 'timestamp_ms': 0, 'seq': 0,
      });

      final nbCount = (await db.rawQuery('SELECT COUNT(*) FROM notebooks'))[0].values.first;
      final noteCount = (await db.rawQuery('SELECT COUNT(*) FROM notes'))[0].values.first;
      final strokeCount = (await db.rawQuery('SELECT COUNT(*) FROM strokes'))[0].values.first;
      final pointCount = (await db.rawQuery('SELECT COUNT(*) FROM stroke_points'))[0].values.first;

      expect(nbCount, 1);
      expect(noteCount, 1);
      expect(strokeCount, 1);
      expect(pointCount, 1);

      await db.close();
    });
  });
}
