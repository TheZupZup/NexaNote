import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:nexanote/screens/note_editor_screen.dart';
import 'package:nexanote/services/api_client.dart';
import 'package:nexanote/widgets/ink_canvas.dart';

/// A plain typed note with a single page of content. None of these layout
/// tests touch the save path (no typing, no delete tap), so the editor never
/// needs an AppState provider — its build/toggle paths read no app state.
Note typedNote({String content = 'Hello world'}) => Note(
      id: 'note-1',
      title: 'Test note',
      noteType: 'typed',
      tags: const [],
      isPinned: false,
      isDeleted: false,
      pageCount: 1,
      updatedAt: '',
      createdAt: '',
      pages: [
        NotePage(
          pageNumber: 1,
          template: 'blank',
          typedContent: content,
          strokes: const [],
        ),
      ],
    );

/// Pumps the editor as it appears on a phone: the editor decides it is "mobile"
/// from MediaQuery width, so we both size the test view and inject system insets
/// (status bar / gesture nav) via a nested MediaQuery for SafeArea to consume.
Future<void> pumpMobile(
  WidgetTester tester,
  Note note, {
  Size size = const Size(360, 740),
  EdgeInsets insets = const EdgeInsets.only(top: 40, bottom: 48),
}) async {
  tester.view.devicePixelRatio = 1.0;
  tester.view.physicalSize = size;
  addTearDown(tester.view.resetPhysicalSize);
  addTearDown(tester.view.resetDevicePixelRatio);

  await tester.pumpWidget(MaterialApp(
    home: Builder(builder: (context) {
      final mq = MediaQuery.of(context);
      return MediaQuery(
        data: mq.copyWith(padding: insets),
        child: NoteEditorScreen(note: note),
      );
    }),
  ));
  await tester.pumpAndSettle();
}

void main() {
  group('NoteEditorScreen responsive layout', () {
    testWidgets('mobile: header clears the status bar and footer clears the '
        'gesture nav via SafeArea', (tester) async {
      await pumpMobile(tester, typedNote());

      // The editor wraps itself in a SafeArea on phones.
      expect(find.byType(SafeArea), findsWidgets);

      // The back button (and thus the whole header) is pushed below the 40px
      // status-bar inset rather than tucked under the notch.
      final backTop = tester.getTopLeft(find.byIcon(Icons.arrow_back)).dy;
      expect(backTop, greaterThanOrEqualTo(40.0));

      // The word/character footer sits above the 48px bottom inset
      // (740 - 48 = 692) instead of under the gesture bar.
      final footerBottom = tester.getBottomLeft(find.text('11 chars')).dy;
      expect(footerBottom, lessThanOrEqualTo(692.0));
    });

    testWidgets('mobile: header, toggle, markdown toolbar and footer all render '
        'without overflow', (tester) async {
      // A 360px wide phone would overflow a fixed-row header; reaching
      // pumpAndSettle without a thrown exception proves it reflows cleanly.
      await pumpMobile(tester, typedNote());

      // Mode toggle (icon-only in the compact phone header).
      expect(find.byIcon(Icons.text_snippet_outlined), findsOneWidget);
      expect(find.byIcon(Icons.draw_outlined), findsOneWidget);
      // Header actions do not overlap the toggle.
      expect(find.byIcon(Icons.delete_outline), findsOneWidget);

      // Markdown toolbar buttons live in a horizontal scroll view.
      expect(find.byIcon(Icons.format_bold), findsOneWidget);
      expect(find.byIcon(Icons.format_quote), findsOneWidget);
      expect(
        find.byWidgetPredicate((w) =>
            w is SingleChildScrollView && w.scrollDirection == Axis.horizontal),
        findsOneWidget,
      );

      // Footer reflects the live content: "Hello world" -> 2 words, 11 chars.
      expect(find.text('2 words'), findsOneWidget);
      expect(find.text('11 chars'), findsOneWidget);
    });

    testWidgets('the Text/Draw toggle swaps the body between markdown and ink',
        (tester) async {
      await pumpMobile(tester, typedNote());

      // Starts in text mode: markdown toolbar present, no ink canvas.
      expect(find.byType(InkCanvas), findsNothing);
      expect(find.byIcon(Icons.format_bold), findsOneWidget);

      // Switch to Draw.
      await tester.tap(find.byIcon(Icons.draw_outlined));
      await tester.pumpAndSettle();
      expect(find.byType(InkCanvas), findsOneWidget);
      expect(find.byIcon(Icons.format_bold), findsNothing);

      // Switch back to Text.
      await tester.tap(find.byIcon(Icons.text_snippet_outlined));
      await tester.pumpAndSettle();
      expect(find.byType(InkCanvas), findsNothing);
      expect(find.byIcon(Icons.format_bold), findsOneWidget);
    });

    testWidgets('wide layout shows full toggle labels and hides the back button',
        (tester) async {
      tester.view.devicePixelRatio = 1.0;
      tester.view.physicalSize = const Size(1200, 800);
      addTearDown(tester.view.resetPhysicalSize);
      addTearDown(tester.view.resetDevicePixelRatio);

      // On wide layouts the editor is embedded in HomeScreen's Scaffold, so we
      // supply one here too (Material ancestor + bounded box).
      await tester.pumpWidget(MaterialApp(
        home: Scaffold(body: NoteEditorScreen(note: typedNote())),
      ));
      await tester.pumpAndSettle();

      // Labels are shown when there is room.
      expect(find.text('Text'), findsOneWidget);
      expect(find.text('Draw'), findsOneWidget);

      // The back button only belongs on the pushed mobile route.
      expect(find.byIcon(Icons.arrow_back), findsNothing);
    });
  });
}
