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

    test('notes table has remote_id and remote_path columns', () async {
      final db = await openDatabase(
        inMemoryDatabasePath,
        version: Schema.version,
        onCreate: Schema.onCreate,
      );

      final cols = await db.rawQuery('PRAGMA table_info(notes)');
      final names = cols.map((c) => c['name'] as String).toSet();
      expect(names, containsAll(['remote_id', 'remote_path']));
      await db.close();
    });
  });

  group('Schema.onUpgrade', () {
    test('adds remote_id/remote_path when migrating v1 → v2', () async {
      final dbPath = inMemoryDatabasePath;

      // Open at v1 with the v1 schema (no remote_id/remote_path).
      final v1 = await openDatabase(
        dbPath,
        version: 1,
        onCreate: (db, _) async {
          await db.execute('''
            CREATE TABLE notes (
              id            TEXT PRIMARY KEY,
              notebook_id   TEXT,
              title         TEXT NOT NULL DEFAULT 'Untitled',
              note_type     TEXT NOT NULL DEFAULT 'typed',
              tags          TEXT NOT NULL DEFAULT '[]',
              typed_content TEXT NOT NULL DEFAULT '',
              is_pinned     INTEGER NOT NULL DEFAULT 0,
              is_archived   INTEGER NOT NULL DEFAULT 0,
              is_deleted    INTEGER NOT NULL DEFAULT 0,
              sync_status   TEXT NOT NULL DEFAULT 'local_only',
              created_at    TEXT NOT NULL,
              updated_at    TEXT NOT NULL
            )
          ''');
        },
      );

      final now = DateTime.utc(2024, 1, 1).toIso8601String();
      await v1.insert('notes', {
        'id': 'legacy', 'title': 'Legacy', 'created_at': now, 'updated_at': now,
      });

      final cols = await v1.rawQuery('PRAGMA table_info(notes)');
      expect(
        cols.map((c) => c['name'] as String).toSet(),
        isNot(contains('remote_id')),
      );

      // Manually upgrade — exercise the same migration code openDatabase
      // would run on a real install.
      await Schema.onUpgrade(v1, 1, Schema.version);

      final upgraded = await v1.rawQuery('PRAGMA table_info(notes)');
      final names = upgraded.map((c) => c['name'] as String).toSet();
      expect(names, containsAll(['remote_id', 'remote_path']));

      // Existing row survives the migration; new columns default to NULL.
      final row =
          (await v1.query('notes', where: 'id = ?', whereArgs: ['legacy'])).first;
      expect(row['title'], 'Legacy');
      expect(row['remote_id'], isNull);
      expect(row['remote_path'], isNull);

      await v1.close();
    });
  });
}
