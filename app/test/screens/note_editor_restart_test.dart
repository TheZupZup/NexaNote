import 'dart:io';
import 'dart:ui' show AppExitResponse;

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:path/path.dart' as p;
import 'package:provider/provider.dart';
import 'package:shared_preferences/shared_preferences.dart';

import 'package:nexanote/data/models/note.dart' as local;
import 'package:nexanote/screens/note_editor_screen.dart';
import 'package:nexanote/services/api_client.dart' as api;
import 'package:nexanote/services/app_state.dart';
import 'package:nexanote/services/local_note_service.dart';
import 'package:nexanote/widgets/ink_canvas.dart';

import '../helpers/fake_backend.dart';
import '../helpers/test_store.dart';

/// One run of the app: its own database connection, backend client and
/// AppState. A new [_Process] on the same database file is what the app
/// sees after being killed and started again.
class _Process {
  _Process(this.state, this.client);
  final AppState state;
  final FakeBackendClient client;
}

void main() {
  late FakeServer server;
  late Directory dir;
  late String dbPath;
  late List<LocalNoteService> stores;

  setUp(() {
    SharedPreferences.setMockInitialValues({
      kPrefsApiUrl: 'http://fake.test',
      kPrefsHasEverConnected: true,
    });
    server = FakeServer();
    dir = Directory.systemTemp.createTempSync('nexanote_restart_');
    dbPath = p.join(dir.path, 'nexanote.db');
    stores = [];
  });

  tearDown(() async {
    for (final store in stores) {
      await store.close();
    }
    dir.deleteSync(recursive: true);
  });

  /// Opening a database file does real file I/O, which only completes
  /// outside the widget test's fake clock; everything after that is
  /// synchronous FFI.
  Future<LocalNoteService> store(WidgetTester tester) async {
    final s = (await tester.runAsync(() => openTestStore(dbPath)))!;
    stores.add(s);
    return s;
  }

  Future<_Process> start(WidgetTester tester,
      {bool backendDown = false}) async {
    final client = FakeBackendClient(server)..down = backendDown;
    final state = AppState(
        localService: await store(tester), clientFactory: (_) => client);
    await state.init();
    return _Process(state, client);
  }

  /// The process dies: it never reaches the backend again and its editor is
  /// gone. Whatever it had not written to disk is lost.
  Future<void> kill(WidgetTester tester, _Process process) async {
    process.client.down = true;
    await tester.pumpWidget(const SizedBox());
  }

  /// What the database file holds for server note [id], read through a
  /// connection of its own.
  Future<(local.Note, int)> onDisk(WidgetTester tester, String id) async {
    final reader = await store(tester);
    final localId = await reader.localIdForRemote(id);
    final note = await reader.getNoteById(localId!);
    final strokes = await reader.getStrokesForNote(localId);
    return (note!, strokes.length);
  }

  Future<void> pumpEditor(
      WidgetTester tester, AppState state, api.Note note) async {
    tester.view.devicePixelRatio = 1.0;
    tester.view.physicalSize = const Size(360, 740);
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
    await tester.pumpWidget(ChangeNotifierProvider<AppState>.value(
      value: state,
      child: MaterialApp(home: NoteEditorScreen(note: note)),
    ));
    await tester.pump();
  }

  Finder titleField() => find.byWidgetPredicate(
      (w) => w is TextField && w.decoration?.hintText == 'Untitled');
  Finder body() => find.byWidgetPredicate(
      (w) => w is TextField && w.decoration?.hintText == 'Start writing...');

  var strokeRow = 0;
  setUp(() => strokeRow = 0);
  Future<void> drawStroke(WidgetTester tester) async {
    final origin = tester.getTopLeft(find.byType(InkCanvas)) +
        Offset(100, 150 + 40.0 * strokeRow++);
    final g = await tester.startGesture(origin);
    await g.moveBy(const Offset(30, 0));
    await g.moveBy(const Offset(30, 0));
    await g.up();
    await tester.pump();
  }

  testWidgets(
      'a drawing made while the backend is down survives a restart and '
      'reaches the server', (tester) async {
    final first = await start(tester);
    final id = server.create('Sketch', 'handwritten').id;
    await pumpEditor(tester, first.state, (await first.state.openNote(id))!);

    first.client.down = true;
    await drawStroke(tester);
    await drawStroke(tester);

    // On disk before the server ever acknowledged it.
    final (stored, strokes) = await onDisk(tester, id);
    expect(strokes, 2);
    expect(stored.inkPending, isTrue);
    expect(server.ink[id], isNull);

    await kill(tester, first);
    final second = await start(tester, backendDown: true);
    final reopened = (await second.state.openNote(id))!;
    await pumpEditor(tester, second.state, reopened);
    expect(tester.widget<InkCanvas>(find.byType(InkCanvas)).initialStrokes,
        hasLength(2));

    second.client.down = false;
    await second.state.connect();
    expect(server.ink[id], hasLength(2));
    expect((await onDisk(tester, id)).$1.inkPending, isFalse);

    // Nothing older shows up later, from either process.
    await tester.pump(const Duration(minutes: 5));
    expect(server.writes, ['ink:2']);
  });

  testWidgets('the final text survives a restart and wins on the server',
      (tester) async {
    final first = await start(tester);
    final id = server.create('Draft', 'typed').id;
    await pumpEditor(tester, first.state, (await first.state.openNote(id))!);

    await tester.enterText(body(), 'version 1');
    await tester.pump(const Duration(seconds: 2));
    expect(server.text[id], 'version 1');

    first.client.down = true;
    await tester.enterText(body(), 'version finale');
    await tester.pump(const Duration(seconds: 2));
    expect((await onDisk(tester, id)).$1.typedContent, 'version finale');
    expect(server.text[id], 'version 1');

    await kill(tester, first);
    final second = await start(tester, backendDown: true);
    final reopened = (await second.state.openNote(id))!;
    expect(reopened.pages!.first.typedContent, 'version finale');
    await pumpEditor(tester, second.state, reopened);
    expect(find.text('version finale'), findsOneWidget);

    second.client.down = false;
    await second.state.connect();
    expect(server.text[id], 'version finale');

    await tester.pump(const Duration(minutes: 5));
    expect(server.text[id], 'version finale');
    expect(server.writes.last, 'text:version finale');
  });

  testWidgets('a new title and text both survive a restart', (tester) async {
    final first = await start(tester);
    final id = server.create('Old title', 'typed').id;
    await pumpEditor(tester, first.state, (await first.state.openNote(id))!);

    first.client.down = true;
    await tester.enterText(titleField(), 'New title');
    await tester.enterText(body(), 'new body');
    await tester.pump(const Duration(seconds: 2));
    final (stored, _) = await onDisk(tester, id);
    expect(stored.title, 'New title');
    expect(stored.typedContent, 'new body');

    await kill(tester, first);
    final second = await start(tester, backendDown: true);
    final reopened = (await second.state.openNote(id))!;
    expect(reopened.title, 'New title');
    expect(reopened.pages!.first.typedContent, 'new body');

    second.client.down = false;
    await second.state.connect();
    expect(server.notes[id]!.title, 'New title');
    expect(server.text[id], 'new body');
    expect(second.state.notes.single.title, 'New title');
    await tester.pump(const Duration(minutes: 5));
  });

  testWidgets(
      'text typed just before Android pauses the app is on disk when the '
      'process is recreated', (tester) async {
    final first = await start(tester);
    final id = server.create('Groceries', 'typed').id;
    await pumpEditor(tester, first.state, (await first.state.openNote(id))!);

    first.client.down = true;
    await tester.enterText(body(), 'milk, eggs');
    // Backgrounded before the autosave debounce: the process may never run
    // again after this.
    tester.binding.handleAppLifecycleStateChanged(AppLifecycleState.paused);
    await tester.pump();
    expect((await onDisk(tester, id)).$1.typedContent, 'milk, eggs');

    await kill(tester, first);
    final second = await start(tester);
    expect(server.text[id], 'milk, eggs');
    final reopened = (await second.state.openNote(id))!;
    expect(reopened.pages!.first.typedContent, 'milk, eggs');
    await tester.pump(const Duration(minutes: 5));
  });

  testWidgets(
      'a drawing made just before Android pauses the app is on disk when the '
      'process is recreated', (tester) async {
    final first = await start(tester);
    final id = server.create('Sketch', 'handwritten').id;
    await pumpEditor(tester, first.state, (await first.state.openNote(id))!);

    first.client.down = true;
    await drawStroke(tester);
    tester.binding.handleAppLifecycleStateChanged(AppLifecycleState.paused);
    await tester.pump();
    expect((await onDisk(tester, id)).$2, 1);

    await kill(tester, first);
    final second = await start(tester, backendDown: true);
    final reopened = (await second.state.openNote(id))!;
    expect(reopened.pages!.first.strokes, hasLength(1));
    second.client.down = false;
    await second.state.connect();
    expect(server.ink[id], hasLength(1));
    await tester.pump(const Duration(minutes: 5));
  });

  testWidgets(
      'closing the desktop window with the backend stuck keeps the edit on '
      'disk and sends it on the next start', (tester) async {
    final first = await start(tester);
    final id = server.create('Plan', 'typed').id;
    await pumpEditor(tester, first.state, (await first.state.openNote(id))!);

    first.client.hold = true; // every save hangs, forever
    await tester.enterText(body(), 'written right before closing');

    AppExitResponse? response;
    tester.binding.handleRequestAppExit().then((r) => response = r);
    await tester.pump();
    // The local write is done before the exit waits on anything else.
    expect((await onDisk(tester, id)).$1.typedContent,
        'written right before closing');
    expect(response, isNull, reason: 'the backend push gets its chance');
    await tester.pump(const Duration(seconds: 6));
    expect(response, AppExitResponse.exit);
    expect(server.text[id], isNull);

    await kill(tester, first);
    final second = await start(tester);
    expect(server.text[id], 'written right before closing');
    final reopened = (await second.state.openNote(id))!;
    expect(reopened.pages!.first.typedContent, 'written right before closing');
    await tester.pump(const Duration(minutes: 5));
  });
}
