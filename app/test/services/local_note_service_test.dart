import 'package:flutter_test/flutter_test.dart';
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

  Future<LocalNoteService> newService() async {
    final service = LocalNoteService(
      databaseOpener: () => openDatabase(
        inMemoryDatabasePath,
        version: Schema.version,
        onCreate: Schema.onCreate,
      ),
    );
    await service.initialize();
    return service;
  }

  group('LocalNoteService.initialize', () {
    test('opens the database and marks the service initialized', () async {
      final service = await newService();
      expect(service.isInitialized, isTrue);
      await service.close();
    });

    test('throws StateError when used before initialize', () async {
      final service = LocalNoteService(
        databaseOpener: () => openDatabase(
          inMemoryDatabasePath,
          version: Schema.version,
          onCreate: Schema.onCreate,
        ),
      );
      expect(service.isInitialized, isFalse);
      expect(service.getNotebooks, throwsStateError);
    });

    test('is idempotent', () async {
      final service = await newService();
      await service.initialize(); // should not throw or reopen
      expect(service.isInitialized, isTrue);
      await service.close();
    });
  });

  group('LocalNoteService notebooks/notes', () {
    late LocalNoteService service;

    setUp(() async {
      service = await newService();
    });

    tearDown(() async {
      await service.close();
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

    tearDown(() async {
      await service.close();
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
  });
}
