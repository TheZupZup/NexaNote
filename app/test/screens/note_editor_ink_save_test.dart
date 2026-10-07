import 'dart:async';
import 'dart:ui' show AppExitResponse;

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:provider/provider.dart';
import 'package:shared_preferences/shared_preferences.dart';

import 'package:nexanote/screens/note_editor_screen.dart';
import 'package:nexanote/services/api_client.dart' as api;
import 'package:nexanote/services/app_state.dart';
import 'package:nexanote/widgets/ink_canvas.dart';

/// Backend whose ink saves can be held open and failed on demand.
class _InkApi extends api.ApiClient {
  _InkApi() : super(baseUrl: 'http://ink.test');

  /// Stroke count of every save attempt, in order.
  final attempts = <int>[];

  /// Stroke count of every save that reached storage, in order.
  final persisted = <int>[];

  bool down = false;
  bool hold = false;
  final _held = <Completer<void>>[];

  Future<void> releaseNext({bool fail = false}) async {
    final gate = _held.removeAt(0);
    fail ? gate.completeError(_error) : gate.complete();
  }

  int get heldCount => _held.length;

  static const _error = api.ApiException('Failed to save drawing', statusCode: 503);

  @override
  Future<void> savePageInk(
      String noteId, int pageNum, List<Map<String, dynamic>> strokes) async {
    attempts.add(strokes.length);
    if (hold) {
      final gate = Completer<void>();
      _held.add(gate);
      await gate.future;
    }
    if (down) throw _error;
    persisted.add(strokes.length);
  }
}

api.Note _sketch() => api.Note(
      id: 'n1',
      title: 'Sketch',
      noteType: 'handwritten',
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
  late _InkApi backend;
  late AppState state;

  setUp(() {
    SharedPreferences.setMockInitialValues({});
    backend = _InkApi();
    state = AppState(clientFactory: (_) => backend);
  });

  Future<void> pumpEditor(WidgetTester tester) async {
    tester.view.devicePixelRatio = 1.0;
    tester.view.physicalSize = const Size(420, 900);
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
    await tester.pumpWidget(ChangeNotifierProvider<AppState>.value(
      value: state,
      child: MaterialApp(home: NoteEditorScreen(note: _sketch())),
    ));
    await tester.pump();
  }

  var strokeRow = 0;
  Future<void> drawStroke(WidgetTester tester) async {
    final origin = tester.getTopLeft(find.byType(InkCanvas)) +
        Offset(100, 150 + 40.0 * strokeRow++);
    final g = await tester.startGesture(origin);
    await g.moveBy(const Offset(30, 0));
    await g.moveBy(const Offset(30, 0));
    await g.up();
    await tester.pump();
  }

  setUp(() => strokeRow = 0);

  testWidgets('a failed last save stays pending and is retried on background',
      (tester) async {
    await pumpEditor(tester);
    backend.down = true;

    await drawStroke(tester);
    expect(backend.attempts, [1]);
    expect(backend.persisted, isEmpty);

    // No new stroke. Going to the background must retry the same snapshot
    // rather than letting the drawing vanish with the process.
    backend.down = false;
    tester.binding.handleAppLifecycleStateChanged(AppLifecycleState.paused);
    await tester.pump();

    expect(backend.persisted, [1]);
  });

  testWidgets('a failed save is retried on its own once the backend is back',
      (tester) async {
    await pumpEditor(tester);
    backend.down = true;
    await drawStroke(tester);
    expect(backend.persisted, isEmpty);

    backend.down = false;
    await tester.pump(const Duration(minutes: 2));

    expect(backend.persisted, [1]);
  });

  testWidgets('only the newest complete drawing is saved after a slow save',
      (tester) async {
    await pumpEditor(tester);
    backend.hold = true;

    await drawStroke(tester); // save of 1 stroke in flight
    await drawStroke(tester); // queued
    await drawStroke(tester); // replaces the queued one
    expect(backend.heldCount, 1);

    await backend.releaseNext();
    await tester.pump();
    expect(backend.heldCount, 1);
    await backend.releaseNext();
    await tester.pump();

    expect(backend.attempts, [1, 3]);
    expect(backend.persisted.last, 3);
  });

  testWidgets('a failure on an older drawing never brings it back',
      (tester) async {
    await pumpEditor(tester);
    backend.hold = true;

    await drawStroke(tester); // 1 stroke in flight
    await drawStroke(tester); // 2 strokes queued
    await backend.releaseNext(fail: true); // the 1-stroke save fails
    await tester.pump();
    await backend.releaseNext(); // the 2-stroke save succeeds
    await tester.pump();

    backend.hold = false;
    await tester.pump(const Duration(minutes: 3)); // any retries run here

    expect(backend.persisted, [2]);
    expect(backend.attempts, [1, 2]);
  });

  testWidgets('a drawing that failed to save is retried after leaving the '
      'editor', (tester) async {
    await pumpEditor(tester);
    backend.down = true;
    await drawStroke(tester);

    await tester.pumpWidget(const SizedBox());
    backend.down = false;
    await tester.pump(const Duration(minutes: 2));

    expect(backend.persisted, [1]);
  });

  testWidgets('retries after leaving the editor stop eventually',
      (tester) async {
    await pumpEditor(tester);
    backend.down = true;
    await drawStroke(tester);

    await tester.pumpWidget(const SizedBox());
    await tester.pump(const Duration(minutes: 30));
    final attempts = backend.attempts.length;
    await tester.pump(const Duration(minutes: 30));

    expect(backend.attempts.length, attempts);
  });

  testWidgets('closing the desktop window waits for the pending drawing',
      (tester) async {
    await pumpEditor(tester);
    backend.hold = true;
    await drawStroke(tester);

    AppExitResponse? response;
    tester.binding.handleRequestAppExit().then((r) => response = r);
    await tester.pump();
    expect(response, isNull, reason: 'exit waits while the save is in flight');

    await backend.releaseNext();
    await tester.pump();

    expect(response, AppExitResponse.exit);
    expect(backend.persisted, [1]);
  });

  testWidgets('closing the window is not blocked forever by a stuck save',
      (tester) async {
    await pumpEditor(tester);
    backend.hold = true;
    await drawStroke(tester);

    AppExitResponse? response;
    tester.binding.handleRequestAppExit().then((r) => response = r);
    await tester.pump(const Duration(seconds: 10));

    expect(response, AppExitResponse.exit);
  });
}
