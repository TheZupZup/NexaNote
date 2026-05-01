import 'package:flutter_test/flutter_test.dart';
import 'package:sqflite/sqflite.dart';
import 'package:sqflite_common_ffi/sqflite_ffi.dart';

import 'package:nexanote/data/database/schema.dart';
import 'package:nexanote/services/app_state.dart';
import 'package:nexanote/services/local_note_service.dart';

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
}
