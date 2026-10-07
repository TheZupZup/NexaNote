import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:provider/provider.dart';
import 'package:shared_preferences/shared_preferences.dart';

import 'package:nexanote/screens/note_editor_screen.dart';
import 'package:nexanote/services/api_client.dart' as api;
import 'package:nexanote/services/app_state.dart';

/// Backend whose text saves only complete when the test says so, so we can
/// type while a save is in flight and complete saves out of order.
class _SlowSaveApi extends api.ApiClient {
  _SlowSaveApi() : super(baseUrl: 'http://slow.test');

  final pending = <Completer<void>>[];
  final started = <String>[];
  final landed = <String>[];

  @override
  Future<void> savePageText(String noteId, int pageNum, String content) async {
    started.add(content);
    final gate = Completer<void>();
    pending.add(gate);
    await gate.future;
    landed.add(content);
  }
}

api.Note _note() => api.Note(
      id: 'n1',
      title: 'Title',
      noteType: 'typed',
      tags: const [],
      isPinned: false,
      isDeleted: false,
      pageCount: 1,
      updatedAt: '',
      createdAt: '',
      pages: [
        api.NotePage(
            pageNumber: 1, template: 'blank', typedContent: '', strokes: []),
      ],
    );

void main() {
  late _SlowSaveApi backend;
  late AppState state;

  setUp(() {
    SharedPreferences.setMockInitialValues({});
    backend = _SlowSaveApi();
    // Not in local mode: saves go through the (slow) backend.
    state = AppState(clientFactory: (_) => backend);
  });

  Finder body() => find.byWidgetPredicate(
      (w) => w is TextField && w.decoration?.hintText == 'Start writing...');

  Future<void> pumpEditor(WidgetTester tester) async {
    tester.view.devicePixelRatio = 1.0;
    tester.view.physicalSize = const Size(360, 740);
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
    await tester.pumpWidget(ChangeNotifierProvider<AppState>.value(
      value: state,
      child: MaterialApp(home: NoteEditorScreen(note: _note())),
    ));
  }

  Future<void> releaseAll(WidgetTester tester) async {
    while (backend.pending.isNotEmpty) {
      backend.pending.removeAt(0).complete();
      await tester.pump();
    }
  }

  testWidgets('text typed while a save is in flight is saved afterwards',
      (tester) async {
    await pumpEditor(tester);

    await tester.enterText(body(), 'first');
    await tester.pump(const Duration(seconds: 2)); // debounce fires
    expect(backend.started, ['first']);

    // Keep typing while the first save is still waiting on the network.
    await tester.enterText(body(), 'first second');
    await tester.pump();

    await releaseAll(tester);
    await tester.pump(const Duration(seconds: 2));
    await releaseAll(tester);
    await tester.pump(const Duration(seconds: 2));
    await releaseAll(tester);

    expect(backend.landed.last, 'first second');
    expect(find.byTooltip('Unsaved'), findsNothing);
    expect(find.byTooltip('Saved'), findsOneWidget);
  });

  testWidgets('an older save never lands after a newer one', (tester) async {
    await pumpEditor(tester);

    await tester.enterText(body(), 'old');
    await tester.pump(const Duration(seconds: 2));
    await tester.enterText(body(), 'new');
    await tester.pump(const Duration(seconds: 2));

    // Complete whatever is in flight newest-first, as a flaky network might.
    while (backend.pending.isNotEmpty) {
      backend.pending.removeLast().complete();
      await tester.pump();
    }
    await tester.pump(const Duration(seconds: 2));
    await releaseAll(tester);

    expect(backend.landed.last, 'new');
  });

  testWidgets('leaving the editor during a save still flushes the last edit',
      (tester) async {
    await pumpEditor(tester);

    await tester.enterText(body(), 'draft');
    await tester.pump(const Duration(seconds: 2));
    await tester.enterText(body(), 'draft final');
    await tester.pump();

    // Leave before the in-flight save finishes.
    await tester.pumpWidget(const SizedBox());
    await releaseAll(tester);
    await releaseAll(tester);

    expect(backend.landed.last, 'draft final');
  });

  for (final lifecycle in [
    AppLifecycleState.inactive,
    AppLifecycleState.hidden,
    AppLifecycleState.paused,
  ]) {
    testWidgets('going to the background ($lifecycle) saves pending text '
        'right away', (tester) async {
      await pumpEditor(tester);

      await tester.enterText(body(), 'typed just before switching apps');
      await tester.pump();
      expect(backend.started, isEmpty); // still inside the 2s debounce

      // Android may kill a backgrounded app without running more Dart code,
      // so the save can't wait for the debounce timer.
      tester.binding.handleAppLifecycleStateChanged(lifecycle);
      await tester.pump();
      await releaseAll(tester);

      expect(backend.landed, ['typed just before switching apps']);
    });
  }
}
