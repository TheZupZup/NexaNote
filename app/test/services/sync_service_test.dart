import 'package:flutter_test/flutter_test.dart';
import 'package:sqflite/sqflite.dart';
import 'package:sqflite_common_ffi/sqflite_ffi.dart';

import 'package:nexanote/data/database/schema.dart';
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
    );
    local = LocalNoteService(database: db);
    await local.initialize();
    fakeApi = FakeApiClient();
    sync = SyncService(apiClient: fakeApi, local: local);
  });

  tearDown(() async {
    await db.close();
  });

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

  test('pullRemote replaces the local store with the API response', () async {
    // Seed local data that should be wiped.
    final stale = await local.createNotebook('Stale');
    await local.createNote('Stale note', notebookId: stale.id);

    final now = DateTime.now().toUtc().toIso8601String();
    fakeApi.remoteNotebooks = [
      api.Notebook(
        id: 'nb-1',
        name: 'Remote NB',
        color: '#112233',
        icon: 'notebook',
        updatedAt: now,
      ),
    ];
    fakeApi.remoteNotes = [
      api.Note(
        id: 'note-1',
        title: 'Remote note',
        noteType: 'typed',
        notebookId: 'nb-1',
        tags: const ['x'],
        isPinned: true,
        isDeleted: false,
        pageCount: 1,
        updatedAt: now,
        createdAt: now,
      ),
    ];

    await sync.pullRemote();

    final notebooks = await local.getNotebooks();
    expect(notebooks, hasLength(1));
    expect(notebooks.first.id, 'nb-1');
    expect(notebooks.first.name, 'Remote NB');
    expect(notebooks.first.syncStatus, 'synced');

    final notes = await local.getNotesForNotebook('nb-1');
    expect(notes, hasLength(1));
    expect(notes.first.id, 'note-1');
    expect(notes.first.title, 'Remote note');
    expect(notes.first.isPinned, isTrue);

    // Stale notebook is gone.
    expect(notebooks.where((n) => n.id == stale.id), isEmpty);
  });

  test('sync runs push then pull and reports counts', () async {
    final nb = await local.createNotebook('Local');
    await local.createNote('To push', notebookId: nb.id);

    final now = DateTime.now().toUtc().toIso8601String();
    fakeApi.remoteNotebooks = [
      api.Notebook(
        id: 'nb-r',
        name: 'After',
        color: '#000000',
        icon: 'notebook',
        updatedAt: now,
      ),
    ];
    fakeApi.remoteNotes = [];

    final result = await sync.sync();

    expect(result.notebooksPushed, 1);
    expect(result.notesPushed, 1);
    expect(result.notebooksPulled, 1);
    expect(result.notesPulled, 0);

    final notebooks = await local.getNotebooks();
    expect(notebooks.single.id, 'nb-r');
  });

  test('exportAllData returns the snapshot used by SyncService', () async {
    final nb = await local.createNotebook('A');
    await local.createNote('one', notebookId: nb.id);
    await local.createNote('two', notebookId: nb.id);

    final snap = await local.exportAllData();
    expect(snap.notebooks, hasLength(1));
    expect(snap.notes, hasLength(2));
  });
}
