import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:provider/provider.dart';
import 'package:shared_preferences/shared_preferences.dart';
import 'package:sqflite_common_ffi/sqflite_ffi.dart';

import 'package:nexanote/data/database/schema.dart';
import 'package:nexanote/main.dart';
import 'package:nexanote/services/api_client.dart' as api;
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

  Future<void> pumpPhone(WidgetTester tester, AppState appState) async {
    tester.view.devicePixelRatio = 1.0;
    tester.view.physicalSize = const Size(400, 800);
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
    await tester.pumpWidget(ChangeNotifierProvider<AppState>.value(
      value: appState,
      child: const NexaNoteApp(),
    ));
    await tester.pumpAndSettle();
  }

  testWidgets('a blank notebook name is not created on phones', (tester) async {
    await pumpPhone(tester, state);

    await tester.tap(find.byTooltip('Open navigation menu'));
    await tester.pumpAndSettle();
    await tester.tap(find.byTooltip('New notebook'));
    await tester.pumpAndSettle();
    await tester.enterText(find.byType(TextField).last, '   ');
    await tester.tap(find.text('Create'));
    await settle(tester);

    expect(state.notebooks, isEmpty);
  });

  testWidgets('a failed note creation is reported, not dropped',
      (tester) async {
    // Backend configured but refusing writes.
    final failing = AppState(
      localService: service,
      clientFactory: (_) => _RefusingApi(),
    );
    await tester.runAsync(() => failing.connect(url: 'http://refusing.test'));
    await pumpPhone(tester, failing);

    await tester.tap(find.byType(FloatingActionButton));
    await settle(tester);

    expect(find.textContaining('Could not create note'), findsOneWidget);
  });

  testWidgets('a search started on the wide layout stays visible and '
      'clearable after switching to the phone layout', (tester) async {
    await tester.runAsync(() async {
      await seed('Alpha', '');
      await seed('Beta', '');
      await state.loadNotes();
    });
    await pumpApp(tester);
    final search = find.byWidgetPredicate(
        (w) => w is TextField && w.decoration?.hintText == 'Search notes...');
    await tester.enterText(search, 'alp');
    await settle(tester);
    expect(find.text('Beta'), findsNothing);

    // Window narrowed / tablet rotated: phone layout, no search field.
    tester.view.physicalSize = const Size(400, 800);
    await settle(tester);
    expect(find.text('Filtered by "alp"'), findsOneWidget);
    await tester.tap(find.byTooltip('Clear search'));
    await settle(tester);
    expect(find.text('Beta'), findsOneWidget);

    // Back to wide: the field shows the current (cleared) query.
    tester.view.physicalSize = const Size(1280, 800);
    await settle(tester);
    expect(find.descendant(of: search, matching: find.text('alp')),
        findsNothing);
  });
}

class _RefusingApi extends api.ApiClient {
  _RefusingApi() : super(baseUrl: 'http://refusing.test');

  @override
  Future<bool> ping() async => true;

  @override
  Future<List<api.Notebook>> getNotebooks() async => [];

  @override
  Future<List<api.Note>> getNotes({
    String? notebookId,
    String? search,
    bool includeDeleted = false,
  }) async =>
      [];

  @override
  Future<api.Note> createNote({
    required String title,
    required String noteType,
    String? notebookId,
    String template = 'blank',
  }) async =>
      throw const api.ApiException('Failed to create note', statusCode: 500);
}
