import 'package:sqflite_common_ffi/sqflite_ffi.dart';

import 'package:nexanote/data/database/schema.dart';
import 'package:nexanote/services/local_note_service.dart';

/// Opens the app's SQLite schema at [path] (in memory by default).
///
/// Uses the FFI factory without a background isolate, so database calls
/// complete inside widget tests' fake clock instead of needing runAsync.
/// Every call opens its own connection, even to the same file: a second
/// open of one file is what a restarted process would see.
Future<Database> openTestDatabase([String path = inMemoryDatabasePath]) {
  sqfliteFfiInit();
  return databaseFactoryFfiNoIsolate.openDatabase(
    path,
    options: OpenDatabaseOptions(
      version: Schema.version,
      onCreate: Schema.onCreate,
      onUpgrade: Schema.onUpgrade,
      singleInstance: false,
    ),
  );
}

/// A [LocalNoteService] over [openTestDatabase].
Future<LocalNoteService> openTestStore([
  String path = inMemoryDatabasePath,
]) async {
  final service = LocalNoteService(database: await openTestDatabase(path));
  await service.initialize();
  return service;
}
