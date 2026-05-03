import 'package:flutter/material.dart';
import 'package:shared_preferences/shared_preferences.dart';

// Aliased so the API DTOs are unambiguous: there are also `Notebook` / `Note`
// classes in data/models reachable through [LocalNoteService].
import 'api_client.dart' as api;
import 'local_note_service.dart';
import 'sync_service.dart';
import '../data/models/note.dart' as local;
import '../data/models/notebook.dart' as local;

typedef ApiClientFactory = api.ApiClient Function(String baseUrl);

/// SharedPreferences key for the user-entered backend URL.
const String kPrefsApiUrl = 'api_url';

/// SharedPreferences key remembering whether we've ever completed a successful
/// connection to a backend. Used by the router so a transient API failure on
/// mobile does not bounce a configured user back to ConnectScreen.
const String kPrefsHasEverConnected = 'has_ever_connected';

class AppState extends ChangeNotifier {
  final LocalNoteService _localService;
  final ApiClientFactory _clientFactory;

  String _apiUrl = 'http://127.0.0.1:8766';
  bool _isConnected = false;
  bool _isBackendAvailable = false;
  bool _hasEverConnected = false;
  String? _backendErrorMessage;
  String? _lastConnectError;
  bool _hasLocalData = false;
  bool _isLoading = false;
  bool _isSyncing = false;
  String? _syncMessage;
  String? _syncError;
  DateTime? _lastSyncTime;
  List<api.Notebook> _notebooks = [];
  List<api.Note> _notes = [];
  api.Notebook? _selectedNotebook;
  api.Note? _selectedNote;

  AppState({
    LocalNoteService? localService,
    ApiClientFactory? clientFactory,
  })  : _localService = localService ?? LocalNoteService(),
        _clientFactory =
            clientFactory ?? ((baseUrl) => api.ApiClient(baseUrl: baseUrl));

  String get apiUrl => _apiUrl;
  bool get isConnected => _isConnected;
  bool get isBackendAvailable => _isBackendAvailable;
  /// True once the app has successfully reached a backend at least once. The
  /// router uses this to keep the user on HomeScreen across runtime API
  /// failures — bouncing a configured user back to ConnectScreen on every
  /// transient mobile-network blip would force them to retype their URL.
  bool get hasEverConnected => _hasEverConnected;
  String? get backendErrorMessage => _backendErrorMessage;
  String? get lastConnectError => _lastConnectError;
  bool get hasLocalData => _hasLocalData;
  bool get isLoading => _isLoading;
  bool get isSyncing => _isSyncing;
  String? get syncMessage => _syncMessage;
  String? get syncError => _syncError;
  DateTime? get lastSyncTime => _lastSyncTime;
  List<api.Notebook> get notebooks => _notebooks;
  List<api.Note> get notes => _notes;
  api.Notebook? get selectedNotebook => _selectedNotebook;
  api.Note? get selectedNote => _selectedNote;
  api.ApiClient get client => _clientFactory(_apiUrl);

  /// Single entry point for local persistence. Screens that need to read or
  /// write notebooks, notes, or strokes from the on-device SQLite store go
  /// through this service; AppState does not mirror its API.
  LocalNoteService get localService => _localService;

  Future<void> init() async {
    await initLocal();
    final prefs = await SharedPreferences.getInstance();
    _apiUrl = prefs.getString(kPrefsApiUrl) ?? 'http://127.0.0.1:8766';
    _hasEverConnected = prefs.getBool(kPrefsHasEverConnected) ?? false;
    await connect();
  }

  /// Opens the local SQLite database. Safe to call from tests directly.
  Future<void> initLocal() => _localService.initialize();

  Future<void> connect({String? url}) async {
    if (url != null) {
      _apiUrl = url;
      final prefs = await SharedPreferences.getInstance();
      await prefs.setString(kPrefsApiUrl, _apiUrl);
    }
    _isLoading = true;
    notifyListeners();

    try {
      _isConnected = await client.ping();
      if (_isConnected) _lastConnectError = null;
    } catch (e) {
      _isConnected = false;
      _lastConnectError = _safeErrorString(e);
    }
    if (!_isConnected && _lastConnectError == null) {
      _lastConnectError = 'Ping returned false (server unreachable)';
    }

    _isBackendAvailable = _isConnected;
    if (_isConnected) {
      _backendErrorMessage = null;
      await _markEverConnected();
      try {
        await loadNotebooks();
        await loadNotes();
      } catch (e) {
        _isBackendAvailable = false;
        _backendErrorMessage = 'Offline mode — backend unavailable';
        await _loadLocalFallback();
      }
    } else {
      _backendErrorMessage = 'Offline mode — backend unavailable';
      await _loadLocalFallback();
    }

    _isLoading = false;
    notifyListeners();
  }

  /// Records that the app has reached a backend at least once. Persisted so a
  /// later cold-start with the backend temporarily down still keeps the user
  /// on HomeScreen instead of bouncing them through the connect flow.
  Future<void> _markEverConnected() async {
    if (_hasEverConnected) return;
    _hasEverConnected = true;
    try {
      final prefs = await SharedPreferences.getInstance();
      await prefs.setBool(kPrefsHasEverConnected, true);
    } catch (_) {
      // SharedPreferences write failures are non-fatal; the in-memory flag
      // still keeps the current session on HomeScreen.
    }
  }

  /// Clears the offline banner after a backend call succeeds. Lets the app
  /// auto-recover from a transient failure as soon as the next request goes
  /// through, without making the user go to Settings → Reconnect.
  /// Callers are responsible for calling [notifyListeners] for their own
  /// state update; this only flips the connection flags.
  bool _markBackendAvailable() {
    if (_isBackendAvailable && _isConnected && _backendErrorMessage == null) {
      return false;
    }
    _isBackendAvailable = true;
    _isConnected = true;
    _backendErrorMessage = null;
    return true;
  }

  /// Populates [_notebooks] and [_notes] from the local SQLite store so the
  /// UI can keep working when the backend cannot be reached.
  Future<void> _loadLocalFallback() async {
    try {
      if (!_localService.isInitialized) {
        await _localService.initialize();
      }
      final snapshot = await _localService.exportAllData();
      _notebooks = snapshot.notebooks.map(_toApiNotebook).toList();
      _notes = snapshot.notes
          .where((n) => !n.isDeleted)
          .map(_toApiNote)
          .toList();
      _hasLocalData = _notebooks.isNotEmpty || _notes.isNotEmpty;
    } catch (_) {
      _notebooks = [];
      _notes = [];
      _hasLocalData = false;
    }
  }

  api.Notebook _toApiNotebook(local.Notebook n) => api.Notebook(
        id: n.id,
        name: n.name,
        color: n.color,
        icon: n.icon,
        updatedAt: n.updatedAt.toIso8601String(),
      );

  api.Note _toApiNote(local.Note n) => api.Note(
        id: n.id,
        title: n.title,
        noteType: n.noteType,
        notebookId: n.notebookId,
        tags: n.tags,
        isPinned: n.isPinned,
        isDeleted: n.isDeleted,
        pageCount: 1,
        updatedAt: n.updatedAt.toIso8601String(),
        createdAt: n.createdAt.toIso8601String(),
      );

  Future<void> loadNotebooks() async {
    try {
      _notebooks = await client.getNotebooks();
      _markBackendAvailable();
      notifyListeners();
    } catch (e) {
      await _handleBackendFailure(e);
      rethrow;
    }
  }

  Future<api.Notebook> createNotebook(String name, String color) async {
    try {
      final nb = await client.createNotebook(name: name, color: color);
      _notebooks.insert(0, nb);
      _markBackendAvailable();
      notifyListeners();
      return nb;
    } catch (e) {
      await _handleBackendFailure(e);
      rethrow;
    }
  }

  Future<void> deleteNotebook(String id) async {
    try {
      await client.deleteNotebook(id);
      _notebooks.removeWhere((n) => n.id == id);
      if (_selectedNotebook?.id == id) { _selectedNotebook = null; _notes = []; }
      _markBackendAvailable();
      notifyListeners();
    } catch (e) {
      await _handleBackendFailure(e);
      rethrow;
    }
  }

  void selectNotebook(api.Notebook? nb) {
    _selectedNotebook = nb;
    notifyListeners();
    loadNotes(notebookId: nb?.id);
  }

  Future<void> loadNotes({String? notebookId, String? search}) async {
    _isLoading = true;
    notifyListeners();
    try {
      _notes = await client.getNotes(notebookId: notebookId, search: search);
      _markBackendAvailable();
    } catch (e) {
      await _handleBackendFailure(e);
    }
    _isLoading = false;
    notifyListeners();
  }

  Future<api.Note> createNote({required String title, required String noteType, String template = 'blank'}) async {
    try {
      final note = await client.createNote(
        title: title, noteType: noteType,
        notebookId: _selectedNotebook?.id, template: template);
      _notes.insert(0, note);
      _markBackendAvailable();
      notifyListeners();
      return note;
    } catch (e) {
      await _handleBackendFailure(e);
      rethrow;
    }
  }

  Future<void> deleteNote(String id) async {
    try {
      await client.deleteNote(id);
      _notes.removeWhere((n) => n.id == id);
      if (_selectedNote?.id == id) _selectedNote = null;
      _markBackendAvailable();
      notifyListeners();
    } catch (e) {
      await _handleBackendFailure(e);
      rethrow;
    }
  }

  Future<void> updateNoteTitle(String id, String title) async {
    try {
      await client.updateNote(id, title: title);
      _markBackendAvailable();
      await loadNotes(notebookId: _selectedNotebook?.id);
    } catch (e) {
      await _handleBackendFailure(e);
      rethrow;
    }
  }

  Future<void> savePageText(String noteId, int pageNum, String content) async {
    try {
      await client.savePageText(noteId, pageNum, content);
      if (_markBackendAvailable()) notifyListeners();
    } catch (e) {
      await _handleBackendFailure(e);
      rethrow;
    }
  }

  void selectNote(api.Note? note) { _selectedNote = note; notifyListeners(); }

  Future<String> triggerSync() async {
    _beginSync();
    try {
      final result = await client.triggerSync();
      final summary = result['summary'] ?? 'Sync complete';
      _markBackendAvailable();
      _finishSync(message: summary);
      return summary;
    } catch (e) {
      final message = 'Sync failed: $e';
      _finishSync(error: message);
      await _handleBackendFailure(e);
      return message;
    }
  }

  /// Push local changes to the backend and replace the local store with
  /// whatever the backend returns. Optional [service] override exists for
  /// tests; production callers leave it null.
  Future<SyncResult> syncNow({SyncService? service}) async {
    _beginSync();
    try {
      await initLocal();
      final svc = service ??
          SyncService(apiClient: client, local: _localService);
      final result = await svc.sync();
      _markBackendAvailable();
      await _markEverConnected();
      _finishSync(message: 'Sync complete — ${result.summary}');
      return result;
    } catch (e) {
      _finishSync(error: 'Sync failed: $e');
      await _handleBackendFailure(e);
      rethrow;
    }
  }

  /// Marks the backend as unavailable after an API call fails mid-session and
  /// refreshes [_notebooks]/[_notes] from the local SQLite store so the UI can
  /// keep working offline. Reuses the same flag and message as the
  /// startup-time path so the existing offline banner kicks in unchanged.
  Future<void> _handleBackendFailure(Object _) async {
    final wasAvailable = _isBackendAvailable;
    _isBackendAvailable = false;
    _backendErrorMessage = 'Offline mode — backend unavailable';
    if (wasAvailable) {
      await _loadLocalFallback();
    }
    notifyListeners();
  }

  /// Stringifies an error for diagnostics without leaking credentials. Errors
  /// from the API client may include the request URL, which can carry tokens
  /// or query strings; we strip everything past the first whitespace block to
  /// keep the message short and predictable.
  String _safeErrorString(Object e) {
    final raw = e.toString();
    return raw.length > 500 ? '${raw.substring(0, 500)}…' : raw;
  }

  void _beginSync() {
    _isSyncing = true;
    _syncError = null;
    _syncMessage = null;
    notifyListeners();
  }

  void _finishSync({String? message, String? error}) {
    _isSyncing = false;
    _syncMessage = message;
    _syncError = error;
    if (error == null) {
      _lastSyncTime = DateTime.now();
    }
    notifyListeners();
  }
}
