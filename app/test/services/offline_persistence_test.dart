import 'package:flutter_test/flutter_test.dart';
import 'package:shared_preferences/shared_preferences.dart';
import 'package:sqflite/sqflite.dart';
import 'package:sqflite_common_ffi/sqflite_ffi.dart';

import 'package:nexanote/data/database/schema.dart';
import 'package:nexanote/services/app_state.dart';
import 'package:nexanote/services/local_note_service.dart';

/// Offline (local-mode) persistence: a user with no backend configured must be
/// able to create a note, type into it, draw on it, and find both the text and
/// the drawing intact when the note is reopened. These tests drive AppState in
/// local mode against an in-memory SQLite database — no HTTP client involved.
void main() {
  setUpAll(() {
    sqfliteFfiInit();
    databaseFactory = databaseFactoryFfi;
  });

  late Database db;
  late LocalNoteService service;
  late AppState state;

  setUp(() async {
    SharedPreferences.setMockInitialValues({});
    db = await openDatabase(
      inMemoryDatabasePath,
      version: Schema.version,
      onCreate: Schema.onCreate,
    );
    service = LocalNoteService(database: db);
    state = AppState(localService: service);
    await state.enableLocalMode();
  });

  tearDown(() async {
    await db.close();
  });

  List<Map<String, dynamic>> sampleStrokes() => [
        {
          'id': 'stroke-a',
          'color': '#2563eb',
          'width': 3.0,
          'tool': 'pen',
          'points': [
            {'x': 1.0, 'y': 2.0, 'pressure': 0.4, 'ts': 0},
            {'x': 3.0, 'y': 4.0, 'pressure': 0.6, 'ts': 16},
          ],
        },
        {
          'id': 'stroke-b',
          'color': '#dc2626',
          'width': 5.0,
          'tool': 'highlighter',
          'points': [
            {'x': 5.0, 'y': 6.0, 'pressure': 0.5, 'ts': 0},
            {'x': 7.0, 'y': 8.0, 'pressure': 0.5, 'ts': 16},
          ],
        },
      ];

  test('offline note can be created and edited without a backend', () async {
    expect(state.localMode, isTrue);
    final note = await state.createNote(title: 'Groceries', noteType: 'typed');
    await state.savePageText(note.id, 1, 'milk, eggs');

    final reopened = await state.getNote(note.id);
    expect(reopened.pages!.first.typedContent, 'milk, eggs');
  });

  test('typed text persists after reopening the note', () async {
    final note = await state.createNote(title: 'Journal', noteType: 'typed');
    await state.savePageText(note.id, 1, 'first line');
    await state.savePageText(note.id, 1, 'first line\nsecond line');

    final reopened = await state.getNote(note.id);
    expect(reopened.pages!.first.typedContent, 'first line\nsecond line');
  });

  test('drawing strokes persist after reopening the note', () async {
    final note = await state.createNote(title: 'Sketch', noteType: 'handwritten');

    await state.savePageInk(note.id, 1, sampleStrokes());

    final reopened = await state.getNote(note.id);
    final strokes = reopened.pages!.first.strokes
        .whereType<Map<String, dynamic>>()
        .toList();
    expect(strokes, hasLength(2));
    // Order is preserved (pen first, highlighter second).
    expect(strokes[0]['id'], 'stroke-a');
    expect(strokes[0]['tool'], 'pen');
    expect(strokes[1]['tool'], 'highlighter');
    // Points round-trip with their pressure/timestamps.
    final firstPoints = (strokes[0]['points'] as List).cast<Map>();
    expect(firstPoints, hasLength(2));
    expect(firstPoints[1]['x'], 3.0);
    expect(firstPoints[1]['ts'], 16);
  });

  test('re-saving ink replaces the previous drawing rather than appending',
      () async {
    final note = await state.createNote(title: 'Redraw', noteType: 'handwritten');
    await state.savePageInk(note.id, 1, sampleStrokes());
    await state.savePageInk(note.id, 1, [sampleStrokes().first]);

    final strokes = await service.getStrokesForNote(note.id);
    expect(strokes, hasLength(1));
    expect(strokes.first.id, 'stroke-a');
  });

  test('saving a drawing marks the note modified for later sync', () async {
    final note = await state.createNote(title: 'Sync me', noteType: 'handwritten');
    // Pretend the note was previously synced.
    final stored = await service.getNoteById(note.id);
    await service.upsertNote(stored!.copyWith(syncStatus: 'synced'));

    await state.savePageInk(note.id, 1, sampleStrokes());

    final after = await service.getNoteById(note.id);
    expect(after!.syncStatus, 'modified');
  });
}
