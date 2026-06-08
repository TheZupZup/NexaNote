import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:provider/provider.dart';
import 'package:shared_preferences/shared_preferences.dart';
import 'package:sqflite/sqflite.dart';
import 'package:sqflite_common_ffi/sqflite_ffi.dart';

import 'package:nexanote/data/database/schema.dart';
import 'package:nexanote/screens/settings_screen.dart';
import 'package:nexanote/services/app_state.dart';
import 'package:nexanote/services/local_note_service.dart';

/// The Settings screen must lay out cleanly on a small phone without overflowing
/// and must keep its content clear of the system bars (SafeArea). Driven in
/// local mode so it never reaches for a backend.
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

  Future<void> pumpSettings(WidgetTester tester, Size size) async {
    tester.view.devicePixelRatio = 1.0;
    tester.view.physicalSize = size;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);

    await tester.pumpWidget(
      ChangeNotifierProvider<AppState>.value(
        value: state,
        child: const MaterialApp(home: SettingsScreen()),
      ),
    );
    await tester.pumpAndSettle();
  }

  testWidgets('renders without overflow on a small phone', (tester) async {
    await pumpSettings(tester, const Size(320, 600));

    // No RenderFlex overflow or other layout exception was thrown.
    expect(tester.takeException(), isNull);
    expect(find.text('Settings'), findsOneWidget);
  });

  testWidgets('wraps its content in a SafeArea so cards clear the system bars',
      (tester) async {
    await pumpSettings(tester, const Size(360, 740));
    expect(find.byType(SafeArea), findsWidgets);
    expect(tester.takeException(), isNull);
  });
}
