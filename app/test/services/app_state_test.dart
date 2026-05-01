import 'package:flutter_test/flutter_test.dart';
import 'package:sqflite/sqflite.dart';
import 'package:sqflite_common_ffi/sqflite_ffi.dart';

import 'package:nexanote/data/database/schema.dart';
import 'package:nexanote/data/models/point.dart';
import 'package:nexanote/data/models/stroke.dart';
import 'package:nexanote/data/repositories/note_repository.dart';
import 'package:nexanote/services/app_state.dart';
import 'package:nexanote/services/local_note_service.dart';

void main() {
  setUpAll(() {
    sqfliteFfiInit();
    databaseFactory = databaseFactoryFfi;
  });

  late Database db;
  late LocalNoteService service;
  late AppState state;

  setUp(() async {
    db = await openDatabase(
      inMemoryDatabasePath,
      version: Schema.version,
      onCreate: Schema.onCreate,
    );
    service = LocalNoteService.withRepository(NoteRepository(db));
    state = AppState(localService: service);
  });

  tearDown(() async {
    await db.close();
  });

  group('AppState local notebooks', () {
    test('loadLocalNotebooks reads from the local repository', () async {
      // Seed via the repository directly so we test the read path.
      await db.insert('notebooks', {
        'id': 'nb-seed',
        'name': 'Seeded',
        'description': '',
        'color': '#abcdef',
        'icon': 'notebook',
        'is_archived': 0,
        'sync_status': 'local_only',
        'created_at': DateTime.utc(2024).toIso8601String(),
        'updated_at': DateTime.utc(2024).toIso8601String(),
      });

      await state.loadLocalNotebooks();

      expect(state.localNotebooks, hasLength(1));
      expect(state.localNotebooks.first.name, 'Seeded');
    });

    test('createLocalNotebook persists and updates the local list',
        () async {
      var notified = 0;
      state.addListener(() => notified++);

      final nb = await state.createLocalNotebook('Ideas', color: '#00ff00');

      expect(state.localNotebooks, hasLength(1));
      expect(state.localNotebooks.first.id, nb.id);
      expect(notified, greaterThan(0));

      // Independently verify the row landed in the database.
      final rows = await db.query('notebooks');
      expect(rows, hasLength(1));
      expect(rows.first['name'], 'Ideas');
    });
  });

  group('AppState local notes', () {
    test('createLocalNote + loadLocalNotesForNotebook reflects new note',
        () async {
      final nb = await state.createLocalNotebook('Journal');
      final note = await state.createLocalNote(
        'Day 1',
        notebookId: nb.id,
      );

      // Newly created notes are inserted at the head of localNotes.
      expect(state.localNotes.first.id, note.id);

      await state.loadLocalNotesForNotebook(nb.id);
      expect(state.localNotes, hasLength(1));
      expect(state.localNotes.first.title, 'Day 1');
    });

    test('getLocalNoteById returns null for an unknown id', () async {
      expect(await state.getLocalNoteById('nope'), isNull);
    });
  });

  group('AppState local strokes', () {
    test('saveStroke persists and getStrokesForNote reloads it', () async {
      final note = await state.createLocalNote('Sketch');
      final stroke = Stroke(
        id: 'stroke-x',
        noteId: note.id,
        createdAt: DateTime.utc(2024, 1, 1),
        points: const [
          StrokePoint(x: 0, y: 0, pressure: 0.5, timestampMs: 0),
          StrokePoint(x: 10, y: 12, pressure: 0.7, timestampMs: 8),
        ],
      );

      await state.saveStroke(stroke);

      final loaded = await state.getStrokesForNote(note.id);
      expect(loaded, hasLength(1));
      expect(loaded.first.id, 'stroke-x');
      expect(loaded.first.points, hasLength(2));
      expect(loaded.first.points[1].y, 12);
    });
  });
}
