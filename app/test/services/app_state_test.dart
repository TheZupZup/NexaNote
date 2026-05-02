import 'package:flutter_test/flutter_test.dart';
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
}
