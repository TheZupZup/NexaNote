import 'package:flutter_test/flutter_test.dart';
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
  Map<String, String> savedText = {};
  Map<String, List<Map<String, dynamic>>> savedInk = {};
  bool failText = false;
  bool failInk = false;

  /// Like the real backend, every write to a note moves its updated_at.
  void _touch(String noteId) {
    remoteNotes = [
      for (final n in remoteNotes)
        n.id == noteId
            ? n.copyWith(
                updatedAt: DateTime.parse(n.updatedAt)
                    .add(const Duration(seconds: 1))
                    .toIso8601String())
            : n,
    ];
  }

  /// Someone edits the note on the server (another device, the web UI).
  void editOnServer(String noteId, {required String text}) {
    savedText[noteId] = text;
    _touch(noteId);
  }

  @override
  Future<void> savePageText(String noteId, int pageNum, String content) async {
    if (failText) throw const api.ApiException('Failed to save text');
    savedText[noteId] = content;
    _touch(noteId);
  }

  @override
  Future<api.Note> getNote(String id) async {
    final note = remoteNotes.where((n) => n.id == id).firstOrNull;
    if (note == null) {
      throw const api.ApiException('Failed to load note', statusCode: 404);
    }
    return note.copyWith(pages: [
      api.NotePage(
        pageNumber: 1,
        template: 'blank',
        typedContent: savedText[id] ?? '',
        strokes: savedInk[id] ?? const [],
      ),
    ]);
  }

  @override
  Future<void> savePageInk(
      String noteId, int pageNum, List<Map<String, dynamic>> strokes) async {
    if (failInk) throw const api.ApiException('Failed to save drawing');
    savedInk[noteId] = strokes;
    _touch(noteId);
  }

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
    final created = api.Notebook(
      id: 'remote-nb-${createdNotebooks.length}',
      name: name,
      color: color,
      icon: 'notebook',
      updatedAt: DateTime.now().toUtc().toIso8601String(),
    );
    // Like the real server, list what was created on the next pull.
    remoteNotebooks = [...remoteNotebooks, created];
    return created;
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
    final created = api.Note(
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
    remoteNotes = [...remoteNotes, created];
    return created;
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

  test('pushLocal uploads the note body and drawing, not just the title',
      () async {
    final note = await local.createNote('Offline', noteType: 'mixed');
    await local.updateNoteContent(note.id, 'written offline');
    await local.replaceStrokesForNote(note.id, [
      Stroke(
        id: 's1',
        noteId: note.id,
        createdAt: DateTime.utc(2024),
        points: const [
          StrokePoint(x: 1, y: 2, pressure: 0.4),
          StrokePoint(x: 3, y: 4, pressure: 0.6),
        ],
      ),
    ]);

    await sync.pushLocal();

    expect(fakeApi.savedText['remote-note-1'], 'written offline');
    expect(fakeApi.savedInk['remote-note-1'], hasLength(1));
    expect(fakeApi.savedInk['remote-note-1']!.first['points'], hasLength(2));
  });

  test('pushLocal files notes under the server id of their notebook',
      () async {
    final nb = await local.createNotebook('Work');
    await local.createNote('Plan', notebookId: nb.id);

    await sync.pushLocal();

    // The server minted its own notebook id; sending the local one would
    // be rejected (404) and abort the push.
    expect(fakeApi.createdNotes.single['notebookId'], 'remote-nb-1');
  });

  test('pushLocal does not resurrect notes deleted before they were pushed',
      () async {
    final note = await local.createNote('Scratch');
    await local.deleteNote(note.id);

    final counts = await sync.pushLocal();

    expect(counts.notes, 0);
    expect(fakeApi.createdNotes, isEmpty);
  });

  test('syncing again after a push does not duplicate notes or notebooks',
      () async {
    final nb = await local.createNotebook('Work');
    await local.createNote('Plan', notebookId: nb.id);

    await sync.sync();
    await sync.sync();
    await sync.sync();

    expect(fakeApi.createdNotebooks, hasLength(1));
    expect(fakeApi.createdNotes, hasLength(1));
    final snapshot = await local.exportAllData();
    expect(snapshot.notes.where((n) => !n.isDeleted), hasLength(1));
    expect(snapshot.notes.single.syncStatus, 'synced');
    expect(snapshot.notebooks, hasLength(1));
  });

  group('resuming an upload interrupted after the create', () {
    Future<String> interruptedUpload() async {
      final note = await local.createNote('Offline');
      await local.updateNoteContent(note.id, 'written offline');
      fakeApi.failText = true;
      await expectLater(sync.pushLocal(), throwsA(isA<api.ApiException>()));
      fakeApi.failText = false;
      return fakeApi.remoteNotes.single.id;
    }

    test('uploads the content into the note it already created', () async {
      final remoteId = await interruptedUpload();

      await sync.pushLocal();

      expect(fakeApi.createdNotes, hasLength(1));
      expect(fakeApi.savedText[remoteId], 'written offline');
    });

    test('never overwrites a remote note that was edited and left empty',
        () async {
      final remoteId = await interruptedUpload();
      // Someone opens the note online and saves it empty on purpose.
      fakeApi.editOnServer(remoteId, text: '');

      await sync.pushLocal();

      // Empty is not proof it is still our untouched placeholder: the
      // remote edit stays, the offline text goes to a separate copy.
      expect(fakeApi.savedText[remoteId], '');
      final copy = fakeApi.createdNotes.last;
      expect(copy['title'], 'Offline (offline copy)');
      expect(fakeApi.savedText['remote-note-2'], 'written offline');
    });

    test('keeps both versions when the remote state cannot be compared',
        () async {
      final remoteId = await interruptedUpload();
      // A server that reports no usable updated_at gives no proof either way.
      fakeApi.remoteNotes = [
        for (final n in fakeApi.remoteNotes)
          n.id == remoteId ? n.copyWith(updatedAt: 'not a date') : n,
      ];

      await sync.pushLocal();

      expect(fakeApi.savedText[remoteId] ?? '', '');
      expect(fakeApi.savedText['remote-note-2'], 'written offline');
    });

    test('a partly uploaded note is not resumed over its own remote copy',
        () async {
      final note = await local.createNote('Sketch', noteType: 'mixed');
      await local.updateNoteContent(note.id, 'caption');
      await local.replaceStrokesForNote(note.id, [
        Stroke(
          id: 's1',
          noteId: note.id,
          createdAt: DateTime.utc(2024),
          points: const [StrokePoint(x: 1, y: 2), StrokePoint(x: 3, y: 4)],
        ),
      ]);
      // Text upload succeeds, the drawing upload fails.
      fakeApi.failInk = true;
      await expectLater(sync.pushLocal(), throwsA(isA<api.ApiException>()));
      fakeApi.failInk = false;
      final remoteId = fakeApi.remoteNotes.single.id;
      expect(fakeApi.savedText[remoteId], 'caption');

      await sync.pushLocal();

      // The remote changed since the create (our own text write is
      // indistinguishable from someone else's), so nothing is overwritten
      // and the complete local version is kept as a copy.
      expect(fakeApi.savedText[remoteId], 'caption');
      expect(fakeApi.savedText['remote-note-2'], 'caption');
      expect(fakeApi.savedInk['remote-note-2'], hasLength(1));
    });

    test('a failed copy upload is resumed into the same copy, not repeated',
        () async {
      final remoteId = await interruptedUpload();
      fakeApi.editOnServer(remoteId, text: 'edited online');

      // The copy gets created but its content upload keeps failing.
      fakeApi.failText = true;
      for (var i = 0; i < 3; i++) {
        await expectLater(sync.pushLocal(), throwsA(isA<api.ApiException>()));
      }
      fakeApi.failText = false;
      await sync.pushLocal();
      await sync.pushLocal();

      final copies = fakeApi.createdNotes
          .where((n) => n['title'] == 'Offline (offline copy)')
          .toList();
      expect(copies, hasLength(1));
      expect(fakeApi.savedText['remote-note-2'], 'written offline');
      expect(fakeApi.savedText[remoteId], 'edited online');
    });

    test('never overwrites what was written in that note online since',
        () async {
      final remoteId = await interruptedUpload();
      fakeApi.editOnServer(remoteId, text: 'typed on the server afterwards');

      await sync.sync();
      await sync.sync();

      expect(fakeApi.savedText[remoteId], 'typed on the server afterwards');
      // The offline text is not dropped: it lands in a separate copy, once.
      final copies = fakeApi.createdNotes
          .where((n) => n['title'] == 'Offline (offline copy)')
          .toList();
      expect(copies, hasLength(1));
      expect(fakeApi.savedText['remote-note-2'], 'written offline');
    });
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
    expect(result.notebooksPulled, 2);
    expect(result.notesPulled, 1);

    // The pushed notebook is now known by the id the server gave it.
    final notebooks = await local.getNotebooks();
    expect(notebooks.map((n) => n.id), containsAll(['remote-nb-1', 'nb-r']));
    expect(notebooks.map((n) => n.id), isNot(contains(nb.id)));
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
