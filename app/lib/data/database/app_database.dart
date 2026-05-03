import 'package:path/path.dart' as p;
import 'package:path_provider/path_provider.dart';
import 'package:sqflite/sqflite.dart';
import 'schema.dart';

/// Opens and holds the single application-level SQLite database.
///
/// Usage in production:
///   final db = await AppDatabase.open();
///   final repo = NoteRepository(db);
///
/// Usage in tests (pass an in-memory path):
///   final db = await AppDatabase.open(path: inMemoryDatabasePath);
///
/// Call [close] on app shutdown to flush any pending writes.
class AppDatabase {
  static Database? _instance;

  /// Returns the shared database, opening it on first call.
  ///
  /// [path] overrides the default documents-directory location; supply
  /// [inMemoryDatabasePath] from sqflite_common_ffi in unit tests.
  static Future<Database> open({String? path}) async {
    if (_instance != null && path == null) return _instance!;

    final dbPath = path ?? await _defaultPath();
    final db = await openDatabase(
      dbPath,
      version: Schema.version,
      onCreate: Schema.onCreate,
      onUpgrade: Schema.onUpgrade,
    );

    if (path == null) _instance = db;
    return db;
  }

  static Future<void> close() async {
    await _instance?.close();
    _instance = null;
  }

  static Future<String> _defaultPath() async {
    final dir = await getApplicationDocumentsDirectory();
    return p.join(dir.path, 'nexanote.db');
  }
}
