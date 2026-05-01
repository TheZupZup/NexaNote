import 'package:flutter_test/flutter_test.dart';
import 'package:sqflite_common_ffi/sqflite_ffi.dart';

import 'package:nexanote/data/database/schema.dart';
import 'package:nexanote/data/repositories/note_repository.dart';
import 'package:nexanote/data/models/stroke.dart';
import 'package:nexanote/data/models/point.dart';

void main() {
  setUpAll(() {
    sqfliteFfiInit();
    databaseFactory = databaseFactoryFfi;
  });

  late dynamic db;
  late NoteRepository repo;

  setUp(() async {
    db = await openDatabase(
      inMemoryDatabasePath,
      version: Schema.version,
      onCreate: Schema.onCreate,
    );
    repo = NoteRepository(db);
  });

  tearDown(() async {
    await db.close();
  });

  // -----------------------------------------------------------------------
  // Notebooks
  // -----------------------------------------------------------------------

  group('createNotebook / getNotebooks', () {
    test('persists a notebook and returns it', () async {
      final nb = await repo.createNotebook('Work', color: '#ff0000');
      expect(nb.id, isNotEmpty);
      expect(nb.name, 'Work');
      expect(nb.color, '#ff0000');

      final all = await repo.getNotebooks();
      expect(all, hasLength(1));
      expect(all.first.name, 'Work');
    });

    test('returns notebooks sorted by name', () async {
      await repo.createNotebook('Zebra');
      await repo.createNotebook('Alpha');
      await repo.createNotebook('Middle');

      final all = await repo.getNotebooks();
      expect(all.map((n) => n.name).toList(), ['Alpha', 'Middle', 'Zebra']);
    });

    test('excludes archived notebooks by default', () async {
      final nb = await repo.createNotebook('Hidden');
      await db.update(
        'notebooks',
        {'is_archived': 1},
        where: 'id = ?',
        whereArgs: [nb.id],
      );

      final visible = await repo.getNotebooks();
      expect(visible, isEmpty);

      final all = await repo.getNotebooks(includeArchived: true);
      expect(all, hasLength(1));
    });
  });

  // -----------------------------------------------------------------------
  // Notes
  // -----------------------------------------------------------------------

  group('createNote / getNotesForNotebook', () {
    test('persists a note linked to a notebook', () async {
      final nb = await repo.createNotebook('NB');
      final note = await repo.createNote('My Note', notebookId: nb.id);

      expect(note.id, isNotEmpty);
      expect(note.title, 'My Note');
      expect(note.notebookId, nb.id);

      final notes = await repo.getNotesForNotebook(nb.id);
      expect(notes, hasLength(1));
      expect(notes.first.title, 'My Note');
    });

    test('returns notes newest first', () async {
      final nb = await repo.createNotebook('NB');
      await repo.createNote('First', notebookId: nb.id);
      // Ensure a distinct updated_at timestamp by nudging the DB directly.
      final second = await repo.createNote('Second', notebookId: nb.id);
      await db.update(
        'notes',
        {'updated_at': DateTime.now().toUtc().add(const Duration(seconds: 1)).toIso8601String()},
        where: 'id = ?',
        whereArgs: [second.id],
      );

      final notes = await repo.getNotesForNotebook(nb.id);
      expect(notes.first.title, 'Second');
    });

    test('excludes notes from other notebooks', () async {
      final nb1 = await repo.createNotebook('NB1');
      final nb2 = await repo.createNotebook('NB2');
      await repo.createNote('Note A', notebookId: nb1.id);
      await repo.createNote('Note B', notebookId: nb2.id);

      expect(await repo.getNotesForNotebook(nb1.id), hasLength(1));
      expect(await repo.getNotesForNotebook(nb2.id), hasLength(1));
    });
  });

  group('getNoteById', () {
    test('returns the correct note', () async {
      final created = await repo.createNote('Solo');
      final found = await repo.getNoteById(created.id);
      expect(found, isNotNull);
      expect(found!.id, created.id);
      expect(found.title, 'Solo');
    });

    test('returns null for unknown id', () async {
      final found = await repo.getNoteById('does-not-exist');
      expect(found, isNull);
    });
  });

  group('deleteNote', () {
    test('soft-deletes: note disappears from getNotesForNotebook', () async {
      final nb = await repo.createNotebook('NB');
      final note = await repo.createNote('Delete me', notebookId: nb.id);

      await repo.deleteNote(note.id);

      final notes = await repo.getNotesForNotebook(nb.id);
      expect(notes, isEmpty);
    });

    test('soft-delete sets is_deleted=1 and sync_status=modified in DB', () async {
      final note = await repo.createNote('Trash');
      await repo.deleteNote(note.id);

      final row = (await db.query('notes', where: 'id = ?', whereArgs: [note.id])).first;
      expect(row['is_deleted'], 1);
      expect(row['sync_status'], 'modified');
    });

    test('note is still retrievable by id after soft-delete', () async {
      final note = await repo.createNote('Soft');
      await repo.deleteNote(note.id);

      final found = await repo.getNoteById(note.id);
      expect(found, isNotNull);
      expect(found!.isDeleted, isTrue);
    });
  });

  // -----------------------------------------------------------------------
  // Strokes
  // -----------------------------------------------------------------------

  group('saveStroke / getStrokesForNote', () {
    test('persists a stroke with its points', () async {
      final note = await repo.createNote('Ink Note');
      final stroke = Stroke(
        id: 'stroke-a',
        noteId: note.id,
        color: '#0000ff',
        width: 3.0,
        tool: 'pen',
        createdAt: DateTime.utc(2024, 1, 1),
        points: [
          const StrokePoint(x: 10.0, y: 20.0, pressure: 0.8, timestampMs: 0),
          const StrokePoint(x: 15.0, y: 25.0, pressure: 0.9, timestampMs: 16),
        ],
      );

      await repo.saveStroke(stroke);

      final strokes = await repo.getStrokesForNote(note.id);
      expect(strokes, hasLength(1));

      final loaded = strokes.first;
      expect(loaded.id, 'stroke-a');
      expect(loaded.color, '#0000ff');
      expect(loaded.tool, 'pen');
      expect(loaded.points, hasLength(2));
      expect(loaded.points[0].x, 10.0);
      expect(loaded.points[1].pressure, 0.9);
    });

    test('points are returned in sequence order', () async {
      final note = await repo.createNote('Seq');
      final stroke = Stroke(
        id: 'stroke-b',
        noteId: note.id,
        createdAt: DateTime.utc(2024, 1, 1),
        points: [
          const StrokePoint(x: 1.0, y: 1.0, timestampMs: 0),
          const StrokePoint(x: 2.0, y: 2.0, timestampMs: 10),
          const StrokePoint(x: 3.0, y: 3.0, timestampMs: 20),
        ],
      );
      await repo.saveStroke(stroke);

      final loaded = (await repo.getStrokesForNote(note.id)).first;
      expect(loaded.points[0].x, 1.0);
      expect(loaded.points[1].x, 2.0);
      expect(loaded.points[2].x, 3.0);
    });

    test('re-saving a stroke replaces its points (no duplicates)', () async {
      final note = await repo.createNote('Upsert');
      final stroke = Stroke(
        id: 'stroke-c',
        noteId: note.id,
        createdAt: DateTime.utc(2024, 1, 1),
        points: [const StrokePoint(x: 1.0, y: 1.0)],
      );
      await repo.saveStroke(stroke);

      final updated = stroke.copyWith(
        points: [
          const StrokePoint(x: 5.0, y: 5.0),
          const StrokePoint(x: 6.0, y: 6.0),
        ],
      );
      await repo.saveStroke(updated);

      final loaded = (await repo.getStrokesForNote(note.id)).first;
      expect(loaded.points, hasLength(2));
      expect(loaded.points[0].x, 5.0);
    });

    test('returns empty list when note has no strokes', () async {
      final note = await repo.createNote('Empty');
      final strokes = await repo.getStrokesForNote(note.id);
      expect(strokes, isEmpty);
    });

    test('returns only strokes for the requested note', () async {
      final noteA = await repo.createNote('A');
      final noteB = await repo.createNote('B');

      await repo.saveStroke(Stroke(
        id: 'stroke-noteA',
        noteId: noteA.id,
        createdAt: DateTime.utc(2024, 1, 1),
      ));

      final strokesA = await repo.getStrokesForNote(noteA.id);
      final strokesB = await repo.getStrokesForNote(noteB.id);
      expect(strokesA, hasLength(1));
      expect(strokesB, isEmpty);
    });
  });
}
