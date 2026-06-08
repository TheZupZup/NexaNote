import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:provider/provider.dart';
import 'package:shared_preferences/shared_preferences.dart';
import 'package:sqflite/sqflite.dart';
import 'package:sqflite_common_ffi/sqflite_ffi.dart';

import 'package:nexanote/data/database/schema.dart';
import 'package:nexanote/data/models/stroke.dart';
import 'package:nexanote/screens/note_editor_screen.dart';
import 'package:nexanote/services/app_state.dart';
import 'package:nexanote/services/local_note_service.dart';
import 'package:nexanote/services/api_client.dart' as api;
import 'package:nexanote/widgets/ink_canvas.dart';

/// End-to-end editor persistence on a phone-sized, offline (local-mode) app.
/// These exercise the two regressions the change targets: the dispose-time text
/// flush, and ink that previously never reached local storage.
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

  Future<void> pumpEditor(WidgetTester tester, api.Note note) async {
    tester.view.devicePixelRatio = 1.0;
    tester.view.physicalSize = const Size(360, 740);
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);

    await tester.pumpWidget(
      ChangeNotifierProvider<AppState>.value(
        value: state,
        child: MaterialApp(home: NoteEditorScreen(note: note)),
      ),
    );
    await tester.pumpAndSettle();
  }

  /// Re-reads [getNoteById] until [predicate] holds or we run out of pumps —
  /// deterministic without depending on exact save-future timing.
  Future<String?> pollContent(
    WidgetTester tester,
    String id,
    bool Function(String?) predicate,
  ) async {
    String? content;
    for (var i = 0; i < 25; i++) {
      content = (await service.getNoteById(id))?.typedContent;
      if (predicate(content)) break;
      await tester.pump(const Duration(milliseconds: 20));
    }
    return content;
  }

  Future<List<Stroke>> pollStrokes(WidgetTester tester, String id) async {
    var strokes = await service.getStrokesForNote(id);
    for (var i = 0; i < 25 && strokes.isEmpty; i++) {
      await tester.pump(const Duration(milliseconds: 20));
      strokes = await service.getStrokesForNote(id);
    }
    return strokes;
  }

  testWidgets('typing then leaving the editor flushes the text to local storage',
      (tester) async {
    final created = await state.createNote(title: 'Notes', noteType: 'typed');
    final note = await state.getNote(created.id);
    await pumpEditor(tester, note);

    await tester.enterText(
        find.byWidgetPredicate((w) =>
            w is TextField && w.decoration?.hintText == 'Start writing...'),
        'remember the milk');
    await tester.pump();

    // Leave the editor *before* the 2s debounce fires by tearing the widget
    // down. The dispose-time flush must still persist the edit.
    await tester.pumpWidget(const SizedBox());
    await tester.pumpAndSettle();

    final content =
        await pollContent(tester, created.id, (c) => c == 'remember the milk');
    expect(content, 'remember the milk');
  });

  testWidgets('debounced autosave persists typed text without leaving',
      (tester) async {
    final created = await state.createNote(title: 'Notes', noteType: 'typed');
    final note = await state.getNote(created.id);
    await pumpEditor(tester, note);

    await tester.enterText(
        find.byWidgetPredicate((w) =>
            w is TextField && w.decoration?.hintText == 'Start writing...'),
        'autosaved body');
    // Let the 2s debounce timer fire.
    await tester.pump(const Duration(seconds: 2));
    await tester.pumpAndSettle();

    final content =
        await pollContent(tester, created.id, (c) => c == 'autosaved body');
    expect(content, 'autosaved body');
  });

  testWidgets('drawing a stroke persists locally and survives a Text/Draw toggle',
      (tester) async {
    final created =
        await state.createNote(title: 'Sketch', noteType: 'handwritten');
    final note = await state.getNote(created.id);
    await pumpEditor(tester, note);

    // Handwritten notes open straight into the ink canvas.
    expect(find.byType(InkCanvas), findsOneWidget);

    // Draw a stroke across the canvas.
    await tester.drag(find.byType(InkCanvas), const Offset(40, 40));
    await tester.pumpAndSettle();

    expect(await pollStrokes(tester, created.id), isNotEmpty);

    // Toggle to Text and back to Draw — the strokes must not be lost.
    await tester.tap(find.byIcon(Icons.text_snippet_outlined));
    await tester.pumpAndSettle();
    expect(find.byType(InkCanvas), findsNothing);

    await tester.tap(find.byIcon(Icons.draw_outlined));
    await tester.pumpAndSettle();

    final canvas = tester.widget<InkCanvas>(find.byType(InkCanvas));
    expect(canvas.initialStrokes, isNotEmpty);

    // And the drawing is still on disk after the round-trip.
    expect(await service.getStrokesForNote(created.id), isNotEmpty);
  });
}
