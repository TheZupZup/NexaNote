import 'package:flutter_test/flutter_test.dart';
import 'package:shared_preferences/shared_preferences.dart';
import 'package:sqflite/sqflite.dart';
import 'package:sqflite_common_ffi/sqflite_ffi.dart';

import 'package:nexanote/data/database/schema.dart';
import 'package:nexanote/services/api_client.dart' as api;
import 'package:nexanote/services/app_state.dart';
import 'package:nexanote/services/local_note_service.dart';
import 'package:nexanote/services/sync_service.dart';

class _StubApi extends api.ApiClient {
  _StubApi({bool shouldThrow = false, this.pingResult = true})
      : shouldThrow = shouldThrow,
        super(baseUrl: 'http://stub.test');
  bool shouldThrow;
  final bool pingResult;
  int createNoteCalls = 0;

  @override
  Future<bool> ping() async {
    if (shouldThrow) throw Exception('unreachable');
    return pingResult;
  }

  @override
  Future<List<api.Notebook>> getNotebooks() async {
    if (shouldThrow) throw Exception('boom');
    return const [];
  }

  @override
  Future<List<api.Note>> getNotes({
    String? notebookId,
    String? search,
    bool includeDeleted = false,
  }) async {
    if (shouldThrow) throw Exception('boom');
    return const [];
  }

  @override
  Future<api.Note> createNote({
    required String title,
    required String noteType,
    String? notebookId,
    String template = 'blank',
  }) async {
    createNoteCalls++;
    if (shouldThrow) throw Exception('boom');
    return api.Note(
      id: 'srv-$createNoteCalls',
      title: title,
      noteType: noteType,
      notebookId: notebookId,
      tags: const [],
      isPinned: false,
      isDeleted: false,
      pageCount: 1,
      updatedAt: '',
      createdAt: '',
    );
  }
}

void main() {
  setUpAll(() {
    sqfliteFfiInit();
    databaseFactory = databaseFactoryFfi;
  });

  late Database db;
  late LocalNoteService service;
  late AppState state;

  setUp(() async {
    db = await openDatabase(
      inMemoryDatabasePath,
      version: Schema.version,
      onCreate: Schema.onCreate,
    );
    service = LocalNoteService(database: db);
    state = AppState(localService: service);
  });

  tearDown(() async {
    await db.close();
  });

  test('initLocal initializes the injected LocalNoteService', () async {
    expect(service.isInitialized, isFalse);
    await state.initLocal();
    expect(service.isInitialized, isTrue);
  });

  test('localService getter exposes the injected service', () {
    expect(state.localService, same(service));
  });

  test('local persistence is reachable through state.localService', () async {
    await state.initLocal();
    final nb = await state.localService.createNotebook('Inbox');
    final all = await state.localService.getNotebooks();
    expect(all.map((n) => n.id), contains(nb.id));
  });

  test('syncNow sets isSyncing during the call and clears it on success',
      () async {
    await state.initLocal();
    final svc = SyncService(apiClient: _StubApi(), local: service);

    expect(state.isSyncing, isFalse);
    final future = state.syncNow(service: svc);
    expect(state.isSyncing, isTrue);

    await future;
    expect(state.isSyncing, isFalse);
    expect(state.syncError, isNull);
    expect(state.syncMessage, isNotNull);
    expect(state.syncMessage, contains('Sync complete'));
    expect(state.lastSyncTime, isNotNull);
  });

  test('lastSyncTime is not set when sync fails', () async {
    await state.initLocal();
    final svc = SyncService(apiClient: _StubApi(shouldThrow: true), local: service);

    expect(state.lastSyncTime, isNull);
    await expectLater(state.syncNow(service: svc), throwsA(isA<Exception>()));
    expect(state.lastSyncTime, isNull);
  });

  test('connect falls back to local data when the backend is unreachable',
      () async {
    await state.initLocal();
    final localNb = await state.localService.createNotebook('Offline NB');
    await state.localService.createNote(
      'Local note',
      notebookId: localNb.id,
    );

    final offlineState = AppState(
      localService: service,
      clientFactory: (_) => _StubApi(shouldThrow: true),
    );

    await offlineState.connect();

    expect(offlineState.isBackendAvailable, isFalse);
    expect(offlineState.isConnected, isFalse);
    expect(offlineState.backendErrorMessage, isNotNull);
    expect(offlineState.backendErrorMessage, contains('Offline'));
    expect(offlineState.hasLocalData, isTrue);
    expect(offlineState.notebooks.map((n) => n.name), contains('Offline NB'));
    expect(offlineState.notes.map((n) => n.title), contains('Local note'));
  });

  test('connect stores a diagnostic-safe error when ping throws', () async {
    await state.initLocal();
    final offlineState = AppState(
      localService: service,
      clientFactory: (_) => _StubApi(shouldThrow: true),
    );

    await offlineState.connect();

    expect(offlineState.isConnected, isFalse);
    expect(offlineState.lastConnectError, isNotNull);
    expect(offlineState.lastConnectError, contains('unreachable'));
    // Should not contain anything that looks credential-shaped.
    final lower = offlineState.lastConnectError!.toLowerCase();
    expect(lower, isNot(contains('password')));
    expect(lower, isNot(contains('token')));
  });

  test('connect clears lastConnectError on successful ping', () async {
    await state.initLocal();
    final stub = _StubApi(shouldThrow: true);
    final s = AppState(localService: service, clientFactory: (_) => stub);

    await s.connect();
    expect(s.lastConnectError, isNotNull);

    stub.shouldThrow = false;
    await s.connect();
    expect(s.lastConnectError, isNull);
  });

  test('connect marks backend as available when ping succeeds', () async {
    await state.initLocal();
    final onlineState = AppState(
      localService: service,
      clientFactory: (_) => _StubApi(),
    );

    await onlineState.connect();

    expect(onlineState.isBackendAvailable, isTrue);
    expect(onlineState.isConnected, isTrue);
    expect(onlineState.backendErrorMessage, isNull);
  });

  test('backend failure mid-session flips isBackendAvailable from true to false',
      () async {
    await state.initLocal();
    final localNb = await service.createNotebook('Local NB');
    await service.createNote('Local note', notebookId: localNb.id);

    final stub = _StubApi();
    final liveState = AppState(
      localService: service,
      clientFactory: (_) => stub,
    );

    await liveState.connect();
    expect(liveState.isBackendAvailable, isTrue);
    expect(liveState.backendErrorMessage, isNull);

    // Simulate the backend going down mid-session.
    stub.shouldThrow = true;
    await expectLater(liveState.loadNotebooks(), throwsA(isA<Exception>()));

    expect(liveState.isBackendAvailable, isFalse);
    expect(liveState.backendErrorMessage, isNotNull);
    expect(liveState.backendErrorMessage, contains('Offline'));
    // Local data is still reachable so the UI does not bounce back to
    // ConnectScreen and the editor keeps working.
    expect(liveState.hasLocalData, isTrue);
    expect(liveState.notebooks.map((n) => n.name), contains('Local NB'));
    expect(liveState.notes.map((n) => n.title), contains('Local note'));

    // SQLite save/load keeps working independently of the backend flag.
    await liveState.localService.createNote('Written offline',
        notebookId: localNb.id);
    final notes =
        await liveState.localService.getNotesForNotebook(localNb.id);
    expect(notes.map((n) => n.title), contains('Written offline'));
  });

  test('syncNow marks backend unavailable when sync fails mid-session',
      () async {
    await state.initLocal();
    final stub = _StubApi();
    final liveState = AppState(
      localService: service,
      clientFactory: (_) => stub,
    );

    await liveState.connect();
    expect(liveState.isBackendAvailable, isTrue);

    stub.shouldThrow = true;
    final svc = SyncService(apiClient: stub, local: service);
    await expectLater(
        liveState.syncNow(service: svc), throwsA(isA<Exception>()));

    expect(liveState.isSyncing, isFalse);
    expect(liveState.syncError, isNotNull);
    expect(liveState.syncError, contains('Sync failed'));
    expect(liveState.isBackendAvailable, isFalse);
    expect(liveState.backendErrorMessage, contains('Offline'));
  });

  test('syncNow records syncError and resets isSyncing when sync fails',
      () async {
    await state.initLocal();
    final svc = SyncService(apiClient: _StubApi(shouldThrow: true), local: service);

    await expectLater(state.syncNow(service: svc), throwsA(isA<Exception>()));

    expect(state.isSyncing, isFalse);
    expect(state.syncMessage, isNull);
    expect(state.syncError, isNotNull);
    expect(state.syncError, contains('Sync failed'));
  });

  // ── Mobile session-stability regression tests ─────────────────────
  //
  // These pin down the routing contract that keeps the user on HomeScreen
  // after a runtime API failure. The router previously bounced any user
  // without local data straight to ConnectScreen on every transient mobile
  // network blip, forcing them to retype their server URL.

  group('mobile session stability', () {
    setUp(() {
      TestWidgetsFlutterBinding.ensureInitialized();
      SharedPreferences.setMockInitialValues({});
    });

    test(
        'connected app stays on HomeScreen (hasEverConnected) after a '
        'runtime API failure', () async {
      await state.initLocal();
      final stub = _StubApi();
      final liveState = AppState(
        localService: service,
        clientFactory: (_) => stub,
      );

      await liveState.connect(url: 'http://192.0.2.10:8766');
      expect(liveState.isBackendAvailable, isTrue);
      expect(liveState.hasEverConnected, isTrue);
      // Fresh remote backend, nothing in the local store yet — exactly the
      // first-action mobile scenario from the bug report.
      expect(liveState.hasLocalData, isFalse);

      stub.shouldThrow = true;
      await expectLater(
          liveState.createNote(title: 'x', noteType: 'typed'),
          throwsA(isA<Exception>()));

      // Backend availability flips and the offline banner shows…
      expect(liveState.isBackendAvailable, isFalse);
      expect(liveState.backendErrorMessage, contains('Offline'));
      // …but the routing flag stays true, so main.dart keeps HomeScreen.
      expect(liveState.hasEverConnected, isTrue,
          reason: 'router must not bounce a configured user to ConnectScreen');
    });

    test('saved server URL is preserved after a backend failure', () async {
      await state.initLocal();
      const remoteUrl = 'http://192.0.2.10:8766';
      final stub = _StubApi();
      final liveState = AppState(
        localService: service,
        clientFactory: (_) => stub,
      );

      await liveState.connect(url: remoteUrl);
      expect(liveState.apiUrl, remoteUrl);

      stub.shouldThrow = true;
      await expectLater(
          liveState.createNote(title: 'x', noteType: 'typed'),
          throwsA(isA<Exception>()));

      // In-memory URL is untouched.
      expect(liveState.apiUrl, remoteUrl);
      // Persisted URL is also untouched — a cold start would reload it.
      final prefs = await SharedPreferences.getInstance();
      expect(prefs.getString(kPrefsApiUrl), remoteUrl);
    });

    test(
        'local-only actions never flip isBackendAvailable or '
        'hasEverConnected', () async {
      await state.initLocal();
      final stub = _StubApi();
      final liveState = AppState(
        localService: service,
        clientFactory: (_) => stub,
      );
      await liveState.connect(url: 'http://192.0.2.10:8766');
      expect(liveState.isBackendAvailable, isTrue);

      // Simulate the user using the offline-first SQLite path: creating a
      // notebook + note locally must not touch the backend connection state.
      final nb = await liveState.localService.createNotebook('Local NB');
      await liveState.localService
          .createNote('Local note', notebookId: nb.id);

      expect(liveState.isBackendAvailable, isTrue);
      expect(liveState.hasEverConnected, isTrue);
      expect(liveState.backendErrorMessage, isNull);
    });

    test(
        'remote URL persists across reconnect when the backend goes down '
        'and comes back', () async {
      await state.initLocal();
      const remoteUrl = 'https://nexanote.example.com';
      final stub = _StubApi();
      final liveState = AppState(
        localService: service,
        clientFactory: (_) => stub,
      );

      // First connect: succeed and save the remote URL.
      await liveState.connect(url: remoteUrl);
      expect(liveState.isBackendAvailable, isTrue);
      expect(liveState.hasEverConnected, isTrue);

      // Backend goes away mid-session.
      stub.shouldThrow = true;
      await expectLater(
          liveState.loadNotebooks(), throwsA(isA<Exception>()));
      expect(liveState.isBackendAvailable, isFalse);
      // URL is still there — the user never has to retype it.
      expect(liveState.apiUrl, remoteUrl);

      // Backend comes back. The next successful API call (without passing
      // a URL) auto-recovers and the saved URL is unchanged.
      stub.shouldThrow = false;
      await liveState.loadNotebooks();
      expect(liveState.isBackendAvailable, isTrue);
      expect(liveState.apiUrl, remoteUrl);
      final prefs = await SharedPreferences.getInstance();
      expect(prefs.getString(kPrefsApiUrl), remoteUrl);
    });

    test(
        'init() restores hasEverConnected from prefs so a cold start with '
        'the backend down still keeps the user on HomeScreen', () async {
      SharedPreferences.setMockInitialValues({
        kPrefsApiUrl: 'http://192.0.2.10:8766',
        kPrefsHasEverConnected: true,
      });

      final coldState = AppState(
        localService: service,
        clientFactory: (_) => _StubApi(shouldThrow: true),
      );
      await coldState.init();

      expect(coldState.isBackendAvailable, isFalse,
          reason: 'backend ping fails');
      expect(coldState.hasEverConnected, isTrue,
          reason: 'router relies on this to skip ConnectScreen');
      expect(coldState.apiUrl, 'http://192.0.2.10:8766');
    });
  });
}
