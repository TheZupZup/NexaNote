import 'package:flutter/gestures.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:nexanote/widgets/ink_canvas.dart';

void main() {
  late List<List<Map<String, dynamic>>> saves;

  Future<void> pumpCanvas(WidgetTester tester) async {
    saves = [];
    tester.view.devicePixelRatio = 1.0;
    tester.view.physicalSize = const Size(800, 900);
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
    await tester.pumpWidget(MaterialApp(
      home: Scaffold(
        body: InkCanvas(
          initialStrokes: const [],
          template: 'blank',
          onStrokesChanged: saves.add,
        ),
      ),
    ));
  }

  Offset canvasPoint(WidgetTester tester, double dx, double dy) =>
      tester.getTopLeft(find.byType(CustomPaint).last) + Offset(dx, dy);

  testWidgets('a one-finger drag draws one stroke', (tester) async {
    await pumpCanvas(tester);
    final g = await tester.startGesture(canvasPoint(tester, 100, 200),
        kind: PointerDeviceKind.touch);
    await g.moveBy(const Offset(30, 0));
    await g.moveBy(const Offset(30, 0));
    await g.up();
    await tester.pump();

    expect(saves, hasLength(1));
    expect(saves.single, hasLength(1));
  });

  testWidgets('a two-finger pinch does not leave a stroke behind',
      (tester) async {
    await pumpCanvas(tester);
    final a = await tester.startGesture(canvasPoint(tester, 100, 200),
        pointer: 1, kind: PointerDeviceKind.touch);
    final b = await tester.startGesture(canvasPoint(tester, 300, 400),
        pointer: 2, kind: PointerDeviceKind.touch);
    for (var i = 0; i < 5; i++) {
      await a.moveBy(const Offset(-10, -10));
      await b.moveBy(const Offset(10, 10));
    }
    await a.up();
    await b.up();
    await tester.pump();

    expect(saves, isEmpty);
  });

  testWidgets('a second finger does not hijack the stroke being drawn',
      (tester) async {
    await pumpCanvas(tester);
    final pen = await tester.startGesture(canvasPoint(tester, 100, 200),
        pointer: 1, kind: PointerDeviceKind.stylus);
    await pen.moveBy(const Offset(20, 0));
    // Palm or finger touches down elsewhere while the stylus is drawing.
    final palm = await tester.startGesture(canvasPoint(tester, 400, 600),
        pointer: 2, kind: PointerDeviceKind.touch);
    await palm.moveBy(const Offset(5, 5));
    await palm.up();
    await pen.moveBy(const Offset(20, 0));
    await pen.up();
    await tester.pump();

    expect(saves, hasLength(1));
    final points = saves.single.single['points'] as List;
    // Only the stylus points, all on the stylus' horizontal line.
    expect(points.map((p) => (p as Map)['y']).toSet(), hasLength(1));
  });

  testWidgets('a right click does not draw', (tester) async {
    await pumpCanvas(tester);
    final g = await tester.startGesture(canvasPoint(tester, 100, 200),
        kind: PointerDeviceKind.mouse, buttons: kSecondaryMouseButton);
    await g.moveBy(const Offset(40, 0));
    await g.up();
    await tester.pump();

    expect(saves, isEmpty);
  });

  testWidgets('a palm already resting on the screen does not block the stylus',
      (tester) async {
    await pumpCanvas(tester);
    final palm = await tester.startGesture(canvasPoint(tester, 400, 600),
        pointer: 1, kind: PointerDeviceKind.touch);
    await palm.moveBy(const Offset(5, 5));
    final pen = await tester.startGesture(canvasPoint(tester, 100, 200),
        pointer: 2, kind: PointerDeviceKind.stylus);
    await pen.moveBy(const Offset(20, 0));
    await pen.moveBy(const Offset(20, 0));
    await pen.up();
    await palm.moveBy(const Offset(5, 5));
    await palm.up();
    await tester.pump();

    expect(saves, hasLength(1));
    final points = saves.single.single['points'] as List;
    expect(points.map((p) => (p as Map)['y']).toSet(), {200.0});
  });
}
