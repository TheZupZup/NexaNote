import 'package:flutter_test/flutter_test.dart';
import 'package:shared_preferences/shared_preferences.dart';

import 'package:nexanote/services/app_state.dart';
import 'package:nexanote/services/local_note_service.dart';
import 'package:nexanote/services/sync_service.dart';

import '../helpers/fake_backend.dart';
import '../helpers/test_store.dart';

/// A device whose local database can't be opened.
class _BrokenStore extends LocalNoteService {
  @override
  Future<void> initialize() async => throw StateError('no SQLite here');
}

void main() {
  late FakeServer server;
  late FakeBackendClient client;
  late LocalNoteService store;
  late AppState state;

  setUp(() async {
    SharedPreferences.setMockInitialValues({});
    server = FakeServer();
    client = FakeBackendClient(server);
    store = await openTestStore();
    state = AppState(localService: store, clientFactory: (_) => client);
    await state.connect(url: 'http://fake.test');
  });

  tearDown(() => store.close());

  Future<String> openedNote() async {
    final id = server.create('Note', 'typed').id;
    await state.openNote(id);
    return id;
  }

  test('an edit is on disk before the backend answers', () async {
    final id = await openedNote();
    client.hold = true;

    final save = state.savePageText(id, 1, 'typed');
    await pumpEventQueue();

    expect(client.heldCount, 1);
    final row = (await store.getNoteById(id))!;
    expect(row.typedContent, 'typed');
    expect(row.textPending, isTrue);

    client.release();
    await save;
    expect((await store.getNoteById(id))!.textPending, isFalse);
  });

  test('the backend confirming save A never marks the later edit B synced',
      () async {
    final id = await openedNote();
    client.hold = true;

    final saveA = state.savePageText(id, 1, 'A');
    await pumpEventQueue();
    await state.recordText(id, 'B'); // typed while A is on the wire
    client.release();
    await saveA;

    final row = (await store.getNoteById(id))!;
    expect(server.text[id], 'A');
    expect(row.typedContent, 'B');
    expect(row.textPending, isTrue, reason: 'B has not been sent yet');

    client.hold = false;
    await state.pushPending(id);
    expect(server.text[id], 'B');
    expect((await store.getNoteById(id))!.textPending, isFalse);
  });

  test(
      'pushes requested while one runs collapse into one with the newest '
      'version', () async {
    final id = await openedNote();
    client.hold = true;

    final first = state.savePageText(id, 1, 'v1');
    await pumpEventQueue();
    final later = [
      state.savePageText(id, 1, 'v2'),
      state.savePageText(id, 1, 'v3'),
    ];
    await pumpEventQueue();
    client.release();
    await pumpEventQueue();
    client.release();
    await Future.wait([first, ...later]);

    expect(server.writes, ['text:v1', 'text:v3']);
  });

  test('opening a note shows a local edit the server does not have yet',
      () async {
    final id = await openedNote();
    client.down = true;
    await state.recordTitle(id, 'Local title');
    await state.recordText(id, 'local text');

    client.down = false;
    final reopened = (await state.openNote(id))!;

    expect(reopened.title, 'Local title');
    expect(reopened.pages!.first.typedContent, 'local text');
    // Opening alone doesn't count as sent.
    expect(server.text[id], isNull);
    expect((await store.getNoteById(id))!.hasPendingEdits, isTrue);
  });

  test('a note opened while the backend is down comes from the local copy',
      () async {
    final id = await openedNote();
    await state.savePageText(id, 1, 'cached');

    client.down = true;
    final offline = (await state.openNote(id))!;

    expect(offline.id, id);
    expect(offline.pages!.first.typedContent, 'cached');
    expect(state.isBackendAvailable, isFalse);
  });

  test('connecting sends edits left over from an earlier run', () async {
    final id = await openedNote();
    client.down = true;
    await expectLater(
        state.savePageText(id, 1, 'left over'), throwsA(anything));

    client.down = false;
    await state.connect();

    expect(server.text[id], 'left over');
    expect(await store.getNotesWithPendingEdits(), isEmpty);
  });

  test('a sync never reverts a pending edit to the server version', () async {
    final id = await openedNote();
    client.down = true;
    await state.recordTitle(id, 'Mine');

    // The server copy moves on meanwhile (another device renames it).
    server.touch(id, title: 'Theirs');
    client.down = false;
    // Pull only: the pending title must survive the merge.
    await SyncService(apiClient: client, local: store).pullRemote();

    expect((await store.getNoteById(id))!.title, 'Mine');
    await state.syncNow();
    expect(server.notes[id]!.title, 'Mine');
  });

  test('deleting a note drops its local copy so nothing is pushed later',
      () async {
    final id = await openedNote();
    client.down = true;
    await state.recordText(id, 'unsent');
    client.down = false;

    await state.deleteNote(id);
    await state.connect();

    expect(await store.localIdForRemote(id), isNull);
    expect(server.writes, isEmpty);
  });

  test('without a usable local store, saves still go to the backend', () async {
    final s =
        AppState(localService: _BrokenStore(), clientFactory: (_) => client);
    await s.connect(url: 'http://fake.test');
    final id = server.create('Note', 'typed').id;

    await s.savePageText(id, 1, 'straight through');
    await s.updateNoteTitle(id, 'Renamed');

    expect(server.text[id], 'straight through');
    expect(server.notes[id]!.title, 'Renamed');
  });

  test(
      'opening a note while its own push is on the wire keeps the local '
      'version', () async {
    final id = await openedNote();
    client.hold = true;
    final push = state.savePageText(id, 1, 'mine');
    await pumpEventQueue();

    // The server still has the old text: the push hasn't landed yet.
    final reopened = (await state.openNote(id))!;
    expect(reopened.pages!.first.typedContent, 'mine');
    expect((await store.getNoteById(id))!.typedContent, 'mine');

    client.release();
    await push;
    expect(server.text[id], 'mine');
  });

  test(
      'a server copy read before our push landed never replaces the local '
      'one', () async {
    final id = await openedNote();
    client.down = true;
    await state.recordText(id, 'mine');
    client.down = false;

    // The read leaves the server before the push lands, its answer comes
    // back after the push was confirmed.
    client.holdReads = true;
    final open = state.openNote(id);
    await pumpEventQueue();
    await state.pushPending(id);
    expect(server.text[id], 'mine');
    client.releaseRead();
    final reopened = (await open)!;

    expect(reopened.pages!.first.typedContent, 'mine');
    expect((await store.getNoteById(id))!.typedContent, 'mine');
  });

  test(
      'an edit to a note deleted on the server is kept and uploaded as a '
      'note of its own', () async {
    final id = await openedNote();
    client.down = true;
    await state.recordText(id, 'kept');
    client.down = false;
    server.notes.remove(id); // deleted from another device

    await expectLater(state.pushPending(id), throwsA(anything));
    final row = (await store.getNoteById(id))!;
    expect(row.remoteId, isNull);
    expect(row.syncStatus, 'local_only');
    expect(row.typedContent, 'kept');

    await state.syncNow();
    final recreated = server.notes.values.single;
    expect(server.text[recreated.id], 'kept');
  });

  test('one note failing to push does not hold back the others', () async {
    final gone = await openedNote();
    final other = await openedNote();
    client.down = true;
    await state.recordText(gone, 'a');
    await state.recordText(other, 'b');
    client.down = false;
    server.notes.remove(gone);

    await state.connect();

    expect(server.text[other], 'b');
    expect(state.syncError, contains('Could not upload edits'));
  });

  test('a push that dies between the text and the drawing resends only the '
      'drawing', () async {
    final id = server.create('Sketch', 'mixed').id;
    await state.openNote(id);
    client.down = true;
    await state.recordText(id, 'caption');
    await state.recordInk(id, [
      {
        'id': 's1',
        'points': [
          {'x': 1, 'y': 2},
        ],
      },
    ]);
    client.down = false;
    client.failInk = true;

    await expectLater(state.pushPending(id), throwsA(anything));
    var row = (await store.getNoteById(id))!;
    expect(row.textPending, isFalse);
    expect(row.inkPending, isTrue);

    client.failInk = false;
    await state.pushPending(id);
    expect(server.writes, ['text:caption', 'ink:1']);
    row = (await store.getNoteById(id))!;
    expect(row.hasPendingEdits, isFalse);
    expect(row.syncStatus, 'synced');
  });

  test('syncing five times in a row sends an edit once and adds nothing',
      () async {
    final id = await openedNote();
    client.down = true;
    await state.recordText(id, 'offline edit');
    client.down = false;

    for (var i = 0; i < 5; i++) {
      await state.syncNow();
    }

    expect(server.writes, ['text:offline edit']);
    expect(server.notes, hasLength(1));
    final rows = (await store.exportAllData()).notes;
    expect(rows, hasLength(1));
    expect(rows.single.typedContent, 'offline edit');
    expect(rows.single.syncStatus, 'synced');
  });
}
