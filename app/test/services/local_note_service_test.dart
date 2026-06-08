import 'package:flutter_test/flutter_test.dart';
import 'package:sqflite/sqflite.dart';
import 'package:sqflite_common_ffi/sqflite_ffi.dart';

import 'package:nexanote/data/database/schema.dart';
import 'package:nexanote/data/models/point.dart';
import 'package:nexanote/data/models/stroke.dart';
import 'package:nexanote/services/local_note_service.dart';

void main() {
  setUpAll(() {
    sqfliteFfiInit();
    databaseFactory = databaseFactoryFfi;
  });

  late Database db;

  setUp(() async {
    db = await openDatabase(
      inMemoryDatabasePath,
      version: Schema.version,
      onCreate: Schema.onCreate,
    );
  });

  tearDown(() async {
    await db.close();
  });

  Future<LocalNoteService> newService() async {
    final service = LocalNoteService(database: db);
    await service.initialize();
    return service;
  }

  group('LocalNoteService.initialize', () {
    test('marks the service initialized', () async {
      final service = await newService();
      expect(service.isInitialized, isTrue);
    });

    test('throws StateError when used before initialize', () {
      final service = LocalNoteService(database: db);
      expect(service.isInitialized, isFalse);
      expect(service.getNotebooks, throwsStateError);
    });

    test('is idempotent', () async {
      final service = await newService();
      await service.initialize();
      expect(service.isInitialized, isTrue);
    });
  });

  group('LocalNoteService notebooks/notes', () {
    late LocalNoteService service;

    setUp(() async {
      service = await newService();
    });

    test('createNotebook persists and getNotebooks returns it', () async {
      final nb = await service.createNotebook('Work', color: '#ff0000');
      final all = await service.getNotebooks();
      expect(all, hasLength(1));
      expect(all.first.id, nb.id);
      expect(all.first.color, '#ff0000');
    });

    test('createNote stored under notebook is retrievable', () async {
      final nb = await service.createNotebook('Personal');
      final note = await service.createNote('Shopping', notebookId: nb.id);

      final notes = await service.getNotesForNotebook(nb.id);
      expect(notes, hasLength(1));
      expect(notes.first.id, note.id);

      final fetched = await service.getNoteById(note.id);
      expect(fetched?.title, 'Shopping');
    });

    test('getNoteById returns null for an unknown id', () async {
      expect(await service.getNoteById('missing'), isNull);
    });
  });

  group('LocalNoteService strokes', () {
    late LocalNoteService service;

    setUp(() async {
      service = await newService();
    });

    test('saveStroke + getStrokesForNote round-trips with points', () async {
      final note = await service.createNote('Ink');
      final stroke = Stroke(
        id: 'stroke-1',
        noteId: note.id,
        color: '#123456',
        width: 2.5,
        tool: 'pen',
        createdAt: DateTime.utc(2024, 1, 1),
        points: const [
          StrokePoint(x: 1.0, y: 2.0, pressure: 0.4, timestampMs: 0),
          StrokePoint(x: 3.0, y: 4.0, pressure: 0.6, timestampMs: 16),
        ],
      );

      await service.saveStroke(stroke);

      final loaded = await service.getStrokesForNote(note.id);
      expect(loaded, hasLength(1));
      expect(loaded.first.id, 'stroke-1');
      expect(loaded.first.points, hasLength(2));
      expect(loaded.first.points[1].x, 3.0);
    });

    test('getStrokesForNote returns empty list for a fresh note', () async {
      final note = await service.createNote('Blank');
      expect(await service.getStrokesForNote(note.id), isEmpty);
    });

    test('replaceStrokesForNote swaps the whole set and drops old points',
        () async {
      final note = await service.createNote('Ink');
      Stroke mk(String id, double x) => Stroke(
            id: id,
            noteId: note.id,
            createdAt: DateTime.utc(2024, 1, 1),
            points: [StrokePoint(x: x, y: 0), StrokePoint(x: x + 1, y: 1)],
          );

      await service.replaceStrokesForNote(note.id, [mk('s1', 0), mk('s2', 10)]);
      expect(await service.getStrokesForNote(note.id), hasLength(2));

      // Replacing with a single stroke removes the others entirely.
      await service.replaceStrokesForNote(note.id, [mk('s3', 20)]);
      final after = await service.getStrokesForNote(note.id);
      expect(after, hasLength(1));
      expect(after.first.id, 's3');
      expect(after.first.points, hasLength(2));

      // Clearing the drawing removes all strokes (and their points).
      await service.replaceStrokesForNote(note.id, const []);
      expect(await service.getStrokesForNote(note.id), isEmpty);
    });
  });
}
