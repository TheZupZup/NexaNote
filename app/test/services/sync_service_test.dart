import 'package:flutter_test/flutter_test.dart';
import 'package:sqflite/sqflite.dart';
import 'package:sqflite_common_ffi/sqflite_ffi.dart';

import 'package:nexanote/data/database/schema.dart';
import 'package:nexanote/data/models/point.dart';
import 'package:nexanote/data/models/stroke.dart';
import 'package:nexanote/services/api_client.dart' as api;
import 'package:nexanote/services/local_note_service.dart';
import 'package:nexanote/services/sync_service.dart';

/// In-memory ApiClient stand-in. Records what SyncService pushes and
/// replays canned responses for the pull half of the cycle.
class FakeApiClient extends api.ApiClient {
  FakeApiClient() : super(baseUrl: 'http://fake.test');

  List<api.Notebook> remoteNotebooks = [];
  List<api.Note> remoteNotes = [];
  List<Map<String, dynamic>> createdNotebooks = [];
  List<Map<String, dynamic>> createdNotes = [];

  @override
  Future<List<api.Notebook>> getNotebooks() async => remoteNotebooks;

  @override
  Future<List<api.Note>> getNotes({
    String? notebookId,
    String? search,
    bool includeDeleted = false,
  }) async =>
      remoteNotes;

  @override
  Future<api.Notebook> createNotebook({
    required String name,
    String color = '#6366f1',
  }) async {
    createdNotebooks.add({'name': name, 'color': color});
    return api.Notebook(
      id: 'remote-nb-${createdNotebooks.length}',
      name: name,
      color: color,
      icon: 'notebook',
      updatedAt: DateTime.now().toUtc().toIso8601String(),
    );
  }

  @override
  Future<api.Note> createNote({
    required String title,
    required String noteType,
    String? notebookId,
    String template = 'blank',
  }) async {
    createdNotes.add({
      'title': title,
      'noteType': noteType,
      'notebookId': notebookId,
    });
    final now = DateTime.now().toUtc().toIso8601String();
    return api.Note(
      id: 'remote-note-${createdNotes.length}',
      title: title,
      noteType: noteType,
      notebookId: notebookId,
      tags: const [],
      isPinned: false,
      isDeleted: false,
      pageCount: 0,
      updatedAt: now,
      createdAt: now,
    );
  }
}

api.Note _remoteNote({
  required String id,
  required String title,
  String noteType = 'typed',
  String? notebookId,
  bool isPinned = false,
  bool isDeleted = false,
  String? updatedAt,
}) {
  final now = updatedAt ?? DateTime.now().toUtc().toIso8601String();
  return api.Note(
    id: id,
    title: title,
    noteType: noteType,
    notebookId: notebookId,
    tags: const [],
    isPinned: isPinned,
    isDeleted: isDeleted,
    pageCount: 0,
    updatedAt: now,
    createdAt: now,
  );
}

api.Notebook _remoteNotebook({
  required String id,
  String name = 'Notebook',
  String color = '#000000',
  String? updatedAt,
}) =>
    api.Notebook(
      id: id,
      name: name,
      color: color,
      icon: 'notebook',
      updatedAt: updatedAt ?? DateTime.now().toUtc().toIso8601String(),
    );

void main() {
  setUpAll(() {
    sqfliteFfiInit();
    databaseFactory = databaseFactoryFfi;
  });

  late Database db;
  late LocalNoteService local;
  late FakeApiClient fakeApi;
  late SyncService sync;

  setUp(() async {
    db = await openDatabase(
      inMemoryDatabasePath,
      version: Schema.version,
      onCreate: Schema.onCreate,
      onUpgrade: Schema.onUpgrade,
    );
    local = LocalNoteService(database: db);
    await local.initialize();
    fakeApi = FakeApiClient();
    sync = SyncService(apiClient: fakeApi, local: local);
  });

  tearDown(() async {
    await db.close();
  });

  // --------------------------------------------------------------------
  // pushLocal
  // --------------------------------------------------------------------

  test('pushLocal sends every local notebook and note to the API', () async {
    final nb = await local.createNotebook('Work', color: '#abcdef');
    await local.createNote('Meeting notes', notebookId: nb.id);
    await local.createNote('Ideas', notebookId: nb.id);

    final counts = await sync.pushLocal();

    expect(counts.notebooks, 1);
    expect(counts.notes, 2);
    expect(fakeApi.createdNotebooks, hasLength(1));
    expect(fakeApi.createdNotebooks.first['name'], 'Work');
    expect(fakeApi.createdNotebooks.first['color'], '#abcdef');
    expect(fakeApi.createdNotes, hasLength(2));
    expect(
      fakeApi.createdNotes.map((n) => n['title']),
      containsAll(['Meeting notes', 'Ideas']),
    );
  });

  test('pushLocal skips records already marked synced', () async {
    fakeApi.remoteNotebooks = [_remoteNotebook(id: 'nb-pulled', name: 'Pulled')];
    fakeApi.remoteNotes = [
      _remoteNote(id: 'note-pulled', title: 'Pulled note', notebookId: 'nb-pulled'),
    ];
    await sync.pullRemote();
    fakeApi.createdNotebooks.clear();
    fakeApi.createdNotes.clear();

    final counts = await sync.pushLocal();

    expect(counts.notebooks, 0);
    expect(counts.notes, 0);
    expect(fakeApi.createdNotebooks, isEmpty);
    expect(fakeApi.createdNotes, isEmpty);
  });

  test('pushLocal does not re-create notes that already carry a remote_id',
      () async {
    // Adopt a remote note, then locally bump its sync_status back to
    // 'modified' (as the editor would on a content edit) — pushLocal
    // must not try to POST it as a brand-new note since the server
    // already owns the row.
    fakeApi.remoteNotes = [_remoteNote(id: 'remote-42', title: 'Hello')];
    await sync.pullRemote();

    await db.update(
      'notes',
      {'sync_status': 'modified'},
      where: 'id = ?',
      whereArgs: ['remote-42'],
    );

    final counts = await sync.pushLocal();
    expect(counts.notes, 0);
    expect(fakeApi.createdNotes, isEmpty);
  });

  // --------------------------------------------------------------------
  // pullRemote — adoption
  // --------------------------------------------------------------------

  test('pullRemote adopts remote notebooks and notes by id', () async {
    final now = DateTime.now().toUtc().toIso8601String();
    fakeApi.remoteNotebooks = [
      _remoteNotebook(id: 'nb-1', name: 'Remote NB', updatedAt: now),
    ];
    fakeApi.remoteNotes = [
      _remoteNote(
        id: 'note-1',
        title: 'Remote note',
        notebookId: 'nb-1',
        isPinned: true,
        updatedAt: now,
      ),
    ];

    await sync.pullRemote();

    final notebooks = await local.getNotebooks();
    expect(notebooks, hasLength(1));
    expect(notebooks.first.id, 'nb-1');
    expect(notebooks.first.syncStatus, 'synced');

    final notes = await local.getNotesForNotebook('nb-1');
    expect(notes, hasLength(1));
    expect(notes.first.id, 'note-1');
    expect(notes.first.title, 'Remote note');
    expect(notes.first.isPinned, isTrue);
    expect(notes.first.remoteId, 'note-1');
    expect(notes.first.syncStatus, 'synced');
  });

  test('pullRemote preserves local-only notes/notebooks the server does not know about',
      () async {
    final localNb = await local.createNotebook('Pristine');
    await local.createNote('Draft', notebookId: localNb.id);

    fakeApi.remoteNotebooks = [_remoteNotebook(id: 'nb-r', name: 'Remote')];
    fakeApi.remoteNotes = [
      _remoteNote(id: 'note-r', title: 'Remote note', notebookId: 'nb-r'),
    ];

    await sync.pullRemote();

    final all = await local.exportAllData();
    expect(all.notebooks.map((n) => n.id), containsAll([localNb.id, 'nb-r']));
    expect(all.notes.map((n) => n.title), containsAll(['Draft', 'Remote note']));
  });

  test('running sync three times after first import does not duplicate notes',
      () async {
    fakeApi.remoteNotebooks = [_remoteNotebook(id: 'nb-1', name: 'NB')];
    fakeApi.remoteNotes = [
      _remoteNote(id: 'note-1', title: 'Hello', notebookId: 'nb-1'),
      _remoteNote(id: 'note-2', title: 'World', notebookId: 'nb-1'),
    ];

    await sync.pullRemote();
    final firstCount = (await local.exportAllData()).notes.length;
    expect(firstCount, 2);

    await sync.pullRemote();
    await sync.pullRemote();

    final after = await local.exportAllData();
    expect(after.notes.length, firstCount);
    expect(after.notebooks.length, 1);
  });

  test('plain Markdown remote ids (md.<base64>) are adopted with stable mapping',
      () async {
    // Mimic what the backend returns for a plain `.md` file dropped on
    // the WebDAV/NAS: synthetic id, raw filename-derived title with
    // slug suffix artifacts.
    fakeApi.remoteNotes = [
      _remoteNote(
        id: 'md.Q2hhdWRyZQ',
        title: 'Chaudré De Saucisses__Md.Q2Hhd',
      ),
    ];

    await sync.pullRemote();
    final after = await sync.pullRemote();
    expect(after.notes, 1);

    final notes = (await local.exportAllData()).notes;
    expect(notes, hasLength(1));
    expect(notes.first.remoteId, 'md.Q2hhdWRyZQ');
    expect(notes.first.title, 'Chaudré De Saucisses');
    expect(notes.first.title, isNot(contains('.md')));
    expect(notes.first.title, isNot(contains('__')));
  });

  test('remote update with same id replaces local fields when newer',
      () async {
    final older = DateTime.utc(2024, 1, 1).toIso8601String();
    final newer = DateTime.utc(2024, 6, 1).toIso8601String();

    fakeApi.remoteNotes = [
      _remoteNote(id: 'note-x', title: 'Old', updatedAt: older),
    ];
    await sync.pullRemote();

    fakeApi.remoteNotes = [
      _remoteNote(id: 'note-x', title: 'New', updatedAt: newer, isPinned: true),
    ];
    await sync.pullRemote();

    final notes = (await local.exportAllData()).notes;
    expect(notes, hasLength(1));
    expect(notes.first.title, 'New');
    expect(notes.first.isPinned, isTrue);
  });

  test('local "modified" notes win over older remote during a pull', () async {
    final older = DateTime.utc(2024, 1, 1).toIso8601String();

    fakeApi.remoteNotes = [_remoteNote(id: 'note-y', title: 'Server', updatedAt: older)];
    await sync.pullRemote();

    final newer = DateTime.utc(2025, 1, 1).toIso8601String();
    await db.update(
      'notes',
      {
        'title': 'Local edit',
        'sync_status': 'modified',
        'updated_at': newer,
      },
      where: 'id = ?',
      whereArgs: ['note-y'],
    );

    fakeApi.remoteNotes = [_remoteNote(id: 'note-y', title: 'Server', updatedAt: older)];
    await sync.pullRemote();

    final note = await local.getNoteById('note-y');
    expect(note!.title, 'Local edit');
    expect(note.syncStatus, 'modified');
  });

  test('same cleaned title with different ids keeps both and flags conflict',
      () async {
    final localNb = await local.createNotebook('NB');
    await local.createNote('Recipes', notebookId: localNb.id);

    fakeApi.remoteNotes = [_remoteNote(id: 'remote-recipes', title: 'Recipes')];

    await sync.pullRemote();

    final notes = (await local.exportAllData()).notes;
    expect(notes, hasLength(2));

    final localCopy = notes.firstWhere((n) => n.id != 'remote-recipes');
    final remoteCopy = notes.firstWhere((n) => n.id == 'remote-recipes');
    expect(localCopy.syncStatus, 'conflict');
    expect(remoteCopy.title, 'Recipes');
  });

  test('synced local note disappearing from remote is removed', () async {
    fakeApi.remoteNotes = [_remoteNote(id: 'note-z', title: 'Bye')];
    await sync.pullRemote();
    expect(await local.getNoteById('note-z'), isNotNull);

    fakeApi.remoteNotes = [];
    await sync.pullRemote();
    expect(await local.getNoteById('note-z'), isNull);
  });

  test('sync runs push then pull and reports counts (push survives, remote adopted)',
      () async {
    final nb = await local.createNotebook('Local');
    await local.createNote('To push', notebookId: nb.id);

    fakeApi.remoteNotebooks = [_remoteNotebook(id: 'nb-r', name: 'After')];
    fakeApi.remoteNotes = [];

    final result = await sync.sync();

    expect(result.notebooksPushed, 1);
    expect(result.notesPushed, 1);
    expect(result.notebooksPulled, 1);
    expect(result.notesPulled, 0);

    final notebooks = await local.getNotebooks();
    expect(notebooks.map((n) => n.id), containsAll([nb.id, 'nb-r']));
  });

  test('pullRemote preserves local strokes (metadata-only sync)', () async {
    final nb = await local.createNotebook('Sketches');
    final note = await local.createNote('Doodle', notebookId: nb.id);
    final stroke = Stroke(
      id: 'stroke-keep-me',
      noteId: note.id,
      color: '#222222',
      width: 1.5,
      tool: 'pen',
      createdAt: DateTime.utc(2024, 1, 1),
      points: const [
        StrokePoint(x: 1.0, y: 2.0, pressure: 0.5, timestampMs: 0),
        StrokePoint(x: 3.0, y: 4.0, pressure: 0.7, timestampMs: 16),
      ],
    );
    await local.saveStroke(stroke);

    fakeApi.remoteNotebooks = [_remoteNotebook(id: nb.id, name: 'Sketches')];
    fakeApi.remoteNotes = [
      _remoteNote(
        id: note.id,
        title: 'Doodle',
        noteType: 'handwritten',
        notebookId: nb.id,
      ),
    ];

    await sync.pullRemote();

    final strokes = await local.getStrokesForNote(note.id);
    expect(strokes, hasLength(1));
    expect(strokes.first.id, 'stroke-keep-me');
    expect(strokes.first.points, hasLength(2));
    expect(strokes.first.points[1].x, 3.0);
  });

  test('exportAllData returns the snapshot used by SyncService', () async {
    final nb = await local.createNotebook('A');
    await local.createNote('one', notebookId: nb.id);
    await local.createNote('two', notebookId: nb.id);

    final snap = await local.exportAllData();
    expect(snap.notebooks, hasLength(1));
    expect(snap.notes, hasLength(2));
  });

  // --------------------------------------------------------------------
  // Title cleanup
  // --------------------------------------------------------------------

  group('SyncService.cleanRemoteTitle', () {
    test('strips a trailing .md', () {
      expect(SyncService.cleanRemoteTitle('Hello.md'), 'Hello');
      expect(SyncService.cleanRemoteTitle('Hello.MD'), 'Hello');
    });

    test('strips a hex __<id-prefix> suffix', () {
      expect(SyncService.cleanRemoteTitle('Foo__abcd1234'), 'Foo');
    });

    test('strips a plain-MD __Md.<token> suffix', () {
      expect(
        SyncService.cleanRemoteTitle('Chaudré De Saucisses__Md.Q2Hhd'),
        'Chaudré De Saucisses',
      );
    });

    test('strips combined __<suffix>.md', () {
      expect(SyncService.cleanRemoteTitle('My Note__abcd1234.md'), 'My Note');
      expect(
        SyncService.cleanRemoteTitle('My Note__Md.Q2Hhd.md'),
        'My Note',
      );
    });

    test('leaves plain titles alone', () {
      expect(SyncService.cleanRemoteTitle('Hello World'), 'Hello World');
      expect(SyncService.cleanRemoteTitle('Foo__bar baz'), 'Foo__bar baz');
    });

    test('keeps the original when stripping would empty the title', () {
      expect(SyncService.cleanRemoteTitle('.md'), '.md');
    });
  });
}
