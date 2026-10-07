import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:provider/provider.dart';
import 'package:shared_preferences/shared_preferences.dart';
import 'package:sqflite_common_ffi/sqflite_ffi.dart';

import 'package:nexanote/data/database/schema.dart';
import 'package:nexanote/main.dart';
import 'package:nexanote/services/app_state.dart';
import 'package:nexanote/services/local_note_service.dart';

/// HomeScreen behaviour on the wide (desktop / Linux) layout, driven through
/// the real app shell in local mode.
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

  Future<void> pumpApp(WidgetTester tester) async {
    tester.view.devicePixelRatio = 1.0;
    tester.view.physicalSize = const Size(1280, 800);
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
    await tester.pumpWidget(ChangeNotifierProvider<AppState>.value(
      value: state,
      child: const NexaNoteApp(),
    ));
    await tester.pumpAndSettle();
  }

  /// Pumps until the real (FFI) database work behind an interaction settles.
  Future<void> settle(WidgetTester tester) async {
    for (var i = 0; i < 10; i++) {
      await tester.runAsync(() => Future.delayed(const Duration(milliseconds: 5)));
      await tester.pump(const Duration(milliseconds: 20));
    }
  }

  Future<void> seed(String title, String body) async {
    final n = await state.createNote(title: title, noteType: 'typed');
    await state.savePageText(n.id, 1, body);
  }

  Finder editorBody() => find.byWidgetPredicate(
      (w) => w is TextField && w.decoration?.hintText == 'Start writing...');

  String editorText(WidgetTester tester) =>
      tester.widget<TextField>(editorBody()).controller!.text;

  testWidgets('selecting another note opens it in the editor panel',
      (tester) async {
    await tester.runAsync(() async {
      await seed('Alpha', 'alpha body');
      await seed('Beta', 'beta body');
      await state.loadNotes();
    });
    await pumpApp(tester);

    await tester.tap(find.text('Alpha'));
    await settle(tester);
    expect(editorText(tester), 'alpha body');

    await tester.tap(find.text('Beta'));
    await settle(tester);
    expect(editorText(tester), 'beta body');
  });

  testWidgets('searching keeps the home screen and the typed query',
      (tester) async {
    await tester.runAsync(() async {
      await seed('Groceries', '');
      await seed('Meeting', '');
      await state.loadNotes();
    });
    await pumpApp(tester);

    final search = find.byWidgetPredicate(
        (w) => w is TextField && w.decoration?.hintText == 'Search notes...');
    await tester.enterText(search, 'groc');
    await tester.pump();
    // The app shell must not swap HomeScreen for the startup splash while the
    // list reloads: that would throw away the search field and the editor.
    expect(find.text('Almost ready...'), findsNothing);
    expect(find.text('Starting...'), findsNothing);
    await settle(tester);

    expect(search, findsOneWidget);
    expect(find.descendant(of: search, matching: find.text('groc')),
        findsOneWidget);
    expect(find.text('Groceries'), findsOneWidget);
    expect(find.text('Meeting'), findsNothing);
  });

  testWidgets('an edit in the open note survives a notes list reload',
      (tester) async {
    await tester.runAsync(() async {
      await seed('Draft', 'v1');
      await state.loadNotes();
    });
    await pumpApp(tester);

    await tester.tap(find.text('Draft'));
    await settle(tester);
    await tester.enterText(editorBody(), 'v2 typed');
    await tester.pump();

    // Something else reloads the list (sync, notebook switch, rename...).
    await tester.runAsync(() => state.loadNotes());
    await settle(tester);

    expect(editorBody(), findsOneWidget);
    expect(editorText(tester), 'v2 typed');
  });
}
