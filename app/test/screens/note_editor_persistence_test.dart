import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:provider/provider.dart';
import 'package:shared_preferences/shared_preferences.dart';
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

  /// Real sqflite (FFI) I/O never completes inside the widget test's fake
  /// clock, so every database read goes through [WidgetTester.runAsync], and
  /// we let real time pass between pumps so the editor's own saves can finish.
  Future<T> io<T>(WidgetTester tester, Future<T> Function() body) async =>
      (await tester.runAsync(body)) as T;

  Future<void> settle(WidgetTester tester) async {
    for (var i = 0; i < 10; i++) {
      await tester.runAsync(
          () => Future<void>.delayed(const Duration(milliseconds: 5)));
      await tester.pump(const Duration(milliseconds: 20));
    }
  }

  Future<String?> pollContent(
    WidgetTester tester,
    String id,
    bool Function(String?) predicate,
  ) async {
    String? content;
    for (var i = 0; i < 25; i++) {
      content = await io(tester, () async {
        return (await service.getNoteById(id))?.typedContent;
      });
      if (predicate(content)) break;
      await settle(tester);
    }
    return content;
  }

  Future<List<Stroke>> pollStrokes(WidgetTester tester, String id) async {
    var strokes = await io(tester, () => service.getStrokesForNote(id));
    for (var i = 0; i < 25 && strokes.isEmpty; i++) {
      await settle(tester);
      strokes = await io(tester, () => service.getStrokesForNote(id));
    }
    return strokes;
  }

  Future<api.Note> newNote(WidgetTester tester, String title, String type) =>
      io(tester, () async {
        final created = await state.createNote(title: title, noteType: type);
        return state.getNote(created.id);
      });

  testWidgets('typing then leaving the editor flushes the text to local storage',
      (tester) async {
    final created = await newNote(tester, 'Notes', 'typed');
    await pumpEditor(tester, created);

    await tester.enterText(
        find.byWidgetPredicate((w) =>
            w is TextField && w.decoration?.hintText == 'Start writing...'),
        'remember the milk');
    await tester.pump();

    // Leave the editor *before* the 2s debounce fires by tearing the widget
    // down. The dispose-time flush must still persist the edit.
    await tester.pumpWidget(const SizedBox());
    await settle(tester);

    final content =
        await pollContent(tester, created.id, (c) => c == 'remember the milk');
    expect(content, 'remember the milk');
  });

  testWidgets('debounced autosave persists typed text without leaving',
      (tester) async {
    final created = await newNote(tester, 'Notes', 'typed');
    await pumpEditor(tester, created);

    await tester.enterText(
        find.byWidgetPredicate((w) =>
            w is TextField && w.decoration?.hintText == 'Start writing...'),
        'autosaved body');
    // Let the 2s debounce timer fire.
    await tester.pump(const Duration(seconds: 2));
    await settle(tester);

    final content =
        await pollContent(tester, created.id, (c) => c == 'autosaved body');
    expect(content, 'autosaved body');
  });

  testWidgets('drawing a stroke persists locally and survives a Text/Draw toggle',
      (tester) async {
    final created = await newNote(tester, 'Sketch', 'handwritten');
    await pumpEditor(tester, created);

    // Handwritten notes open straight into the ink canvas.
    expect(find.byType(InkCanvas), findsOneWidget);

    // Draw a stroke across the canvas.
    await tester.drag(find.byType(InkCanvas), const Offset(40, 40));
    await settle(tester);

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
    expect(await io(tester, () => service.getStrokesForNote(created.id)),
        isNotEmpty);
  });

  testWidgets('deleting from the editor asks for confirmation first',
      (tester) async {
    final created = await newNote(tester, 'Keep me', 'typed');
    await pumpEditor(tester, created);

    Future<bool> isDeleted() => io(tester, () async {
          return (await service.getNoteById(created.id))!.isDeleted;
        });

    await tester.tap(find.byTooltip('Delete note'));
    await tester.pumpAndSettle();
    expect(find.text('Delete note?'), findsOneWidget);
    await tester.tap(find.text('Cancel'));
    await tester.pumpAndSettle();
    await settle(tester);
    expect(await isDeleted(), isFalse);

    await tester.tap(find.byTooltip('Delete note'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Delete'));
    await settle(tester);
    expect(await isDeleted(), isTrue);
  });
}
