import 'package:flutter_test/flutter_test.dart';
import 'package:sqflite/sqflite.dart';
import 'package:sqflite_common_ffi/sqflite_ffi.dart';

import 'package:nexanote/data/database/schema.dart';
import 'package:nexanote/services/api_client.dart' as api;
import 'package:nexanote/services/app_state.dart';
import 'package:nexanote/services/local_note_service.dart';
import 'package:nexanote/services/sync_service.dart';

class _StubApi extends api.ApiClient {
  _StubApi({this.shouldThrow = false}) : super(baseUrl: 'http://stub.test');
  final bool shouldThrow;

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
