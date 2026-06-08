import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:provider/provider.dart';
import 'package:shared_preferences/shared_preferences.dart';
import 'package:sqflite/sqflite.dart';
import 'package:sqflite_common_ffi/sqflite_ffi.dart';

import 'package:nexanote/data/database/schema.dart';
import 'package:nexanote/main.dart';
import 'package:nexanote/services/api_client.dart' as api;
import 'package:nexanote/services/app_state.dart';
import 'package:nexanote/services/local_note_service.dart';

/// A backend that is never reachable. The onboarding routing tests must work
/// with no server at all, so any accidental ping returns false rather than
/// hitting the network.
class _OfflineStubApi extends api.ApiClient {
  _OfflineStubApi() : super(baseUrl: 'http://stub.test');

  @override
  Future<bool> ping() async => false;
}

void main() {
  setUpAll(() {
    sqfliteFfiInit();
    databaseFactory = databaseFactoryFfi;
  });

  late Database db;
  late LocalNoteService service;

  setUp(() async {
    TestWidgetsFlutterBinding.ensureInitialized();
    SharedPreferences.setMockInitialValues({});
    db = await openDatabase(
      inMemoryDatabasePath,
      version: Schema.version,
      onCreate: Schema.onCreate,
    );
    service = LocalNoteService(database: db);
  });

  tearDown(() async {
    await db.close();
  });

  Widget app(AppState state) => ChangeNotifierProvider<AppState>.value(
        value: state,
        child: const NexaNoteApp(),
      );

  testWidgets('first launch leads with the "Use offline" option', (tester) async {
    final state = AppState(
      localService: service,
      clientFactory: (_) => _OfflineStubApi(),
    );
    await state.initLocal();
    expect(state.needsOnboarding, isTrue);

    await tester.pumpWidget(app(state));
    await tester.pumpAndSettle();

    // The welcome screen offers offline use; HomeScreen's FAB isn't here yet.
    expect(find.text('Use NexaNote offline'), findsOneWidget);
    expect(find.text('Connect to server'), findsOneWidget);
    expect(find.byType(FloatingActionButton), findsNothing);
  });

  testWidgets('tapping "Use offline" enters local mode and opens HomeScreen',
      (tester) async {
    final state = AppState(
      localService: service,
      clientFactory: (_) => _OfflineStubApi(),
    );
    await state.initLocal();

    await tester.pumpWidget(app(state));
    await tester.pumpAndSettle();
    expect(find.text('Use NexaNote offline'), findsOneWidget);

    // enableLocalMode touches the isolate-backed local store, so dispatch the
    // tap and let the real async work complete inside runAsync.
    await tester.runAsync(() async {
      await tester.tap(find.text('Use NexaNote offline'));
      await Future<void>.delayed(const Duration(milliseconds: 100));
    });

    // The button is wired to enableLocalMode — local mode is now on.
    expect(state.localMode, isTrue);

    // And the router has rebuilt to HomeScreen (mobile layout shows a FAB).
    await tester.pumpAndSettle();
    expect(find.byType(FloatingActionButton), findsOneWidget);
    expect(find.text('Use NexaNote offline'), findsNothing);
  });

  testWidgets('a local-mode session opens HomeScreen directly (no onboarding)',
      (tester) async {
    final state = AppState(
      localService: service,
      clientFactory: (_) => _OfflineStubApi(),
    );
    // enableLocalMode reads the isolate-backed local store, so run it inside
    // runAsync; once it's done the widget pump only touches in-memory state.
    await tester.runAsync(() async {
      await state.enableLocalMode();
    });
    expect(state.needsOnboarding, isFalse);

    await tester.pumpWidget(app(state));
    await tester.pumpAndSettle();

    expect(find.byType(FloatingActionButton), findsOneWidget);
    expect(find.text('Use NexaNote offline'), findsNothing);
  });
}
