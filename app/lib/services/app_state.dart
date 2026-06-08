import 'package:flutter/material.dart';
import 'package:shared_preferences/shared_preferences.dart';

// Aliased so the API DTOs are unambiguous: there are also `Notebook` / `Note`
// classes in data/models reachable through [LocalNoteService].
import 'api_client.dart' as api;
import 'local_note_service.dart';
import 'server_url.dart';
import 'sync_service.dart';
import '../data/models/note.dart' as local;
import '../data/models/notebook.dart' as local;
import '../data/models/stroke.dart';
import '../data/models/point.dart';

typedef ApiClientFactory = api.ApiClient Function(String baseUrl);

/// SharedPreferences key for the user-entered backend URL.
const String kPrefsApiUrl = 'api_url';

/// SharedPreferences key for an optional WebDAV URL override. When unset, the
/// app derives the WebDAV URL from [kPrefsApiUrl] by appending `/webdav` —
/// matching the unified-backend reverse-proxy layout. Advanced users can set
/// this explicitly (e.g. `http://192.168.1.10:8765`) to keep the legacy
/// two-port deployment working.
const String kPrefsWebdavUrlOverride = 'webdav_url_override';

/// SharedPreferences key remembering whether we've ever completed a successful
/// connection to a backend. Used by the router so a transient API failure on
/// mobile does not bounce a configured user back to ConnectScreen.
const String kPrefsHasEverConnected = 'has_ever_connected';

/// SharedPreferences key remembering that the user chose to run NexaNote
/// without a backend ("local mode"). This is deliberately distinct from a
/// transient backend-unavailable state: local mode means *no backend is
/// configured*, so the app uses on-device SQLite as the source of truth,
/// never surfaces a scary "backend unavailable" banner, and treats sync as a
/// friendly no-op until the user connects a server. Cleared automatically the
/// first time a connection succeeds.
const String kPrefsLocalMode = 'local_mode';

/// Message shown when the user triggers sync while no backend is configured.
/// WebDAV/NAS sync is mediated by the backend, so there is nothing to sync
/// against in local mode — we say so plainly instead of surfacing an error.
const String kLocalModeSyncMessage =
    'Configure sync in Settings to use WebDAV/NAS sync.';

class AppState extends ChangeNotifier {
  final LocalNoteService _localService;
  final ApiClientFactory _clientFactory;

  String _apiUrl = 'http://127.0.0.1:8766';
  String? _webdavUrlOverride;
  bool _isConnected = false;
  bool _isBackendAvailable = false;
  bool _hasEverConnected = false;
  bool _localMode = false;
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

  /// The effective WebDAV URL. Returns the user-set override if any, otherwise
  /// derives from [apiUrl] by appending `/webdav` so a single base URL behind
  /// a reverse proxy serves both the API (`/`) and WebDAV (`/webdav`).
  String get webdavUrl =>
      _webdavUrlOverride ?? ServerUrl.deriveWebdavUrl(_apiUrl);

  /// True when the WebDAV URL is auto-derived from [apiUrl] (no explicit
  /// override). Used by Settings to show a "derived from API URL" hint.
  bool get isWebdavUrlDerived => _webdavUrlOverride == null;
  bool get isConnected => _isConnected;
  bool get isBackendAvailable => _isBackendAvailable;
  /// True once the app has successfully reached a backend at least once. The
  /// router uses this to keep the user on HomeScreen across runtime API
  /// failures — bouncing a configured user back to ConnectScreen on every
  /// transient mobile-network blip would force them to retype their URL.
  bool get hasEverConnected => _hasEverConnected;

  /// True when the user is running NexaNote without a backend — either they
  /// chose "Use offline" at first launch or have not configured a server yet.
  /// In this mode CRUD goes straight to the local SQLite store and sync is a
  /// friendly no-op. Cleared as soon as [connect] reaches a backend.
  bool get localMode => _localMode;

  /// True when a backend has been configured (i.e. the app is not in local
  /// mode). Lets Settings and the UI distinguish "backend not configured"
  /// (local mode) from "backend configured but currently unavailable".
  bool get isBackendConfigured => !_localMode;

  /// Whether the first-launch onboarding screen should be shown. A fresh
  /// install — no backend ever reached, no explicit local-mode choice, and no
  /// local data — lands here; everyone else goes straight to HomeScreen. The
  /// `hasEverConnected` / `hasLocalData` terms preserve the existing
  /// escape hatches so users upgrading from older builds are never bounced
  /// back through onboarding.
  bool get needsOnboarding =>
      !_hasEverConnected && !_localMode && !_hasLocalData;
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
    final override = prefs.getString(kPrefsWebdavUrlOverride);
    _webdavUrlOverride =
        (override == null || override.trim().isEmpty) ? null : override;
    _hasEverConnected = prefs.getBool(kPrefsHasEverConnected) ?? false;
    _localMode = prefs.getBool(kPrefsLocalMode) ?? false;

    if (_localMode) {
      // No backend configured — skip the ping (it would only manufacture a
      // scary "backend unavailable" state for a user who deliberately chose
      // offline). Load the on-device store and open straight into HomeScreen.
      _isBackendAvailable = false;
      _backendErrorMessage = null;
      await _loadLocalFallback();
      notifyListeners();
      return;
    }

    await connect();
  }

  /// Sets or clears the explicit WebDAV URL override. Pass `null` or a blank
  /// string to fall back to the derived value (`apiUrl + "/webdav"`).
  Future<void> setWebdavUrlOverride(String? url) async {
    final normalized = (url == null || url.trim().isEmpty) ? null : url.trim();
    _webdavUrlOverride = normalized;
    final prefs = await SharedPreferences.getInstance();
    if (normalized == null) {
      await prefs.remove(kPrefsWebdavUrlOverride);
    } else {
      await prefs.setString(kPrefsWebdavUrlOverride, normalized);
    }
    notifyListeners();
  }

  /// Opens the local SQLite database. Safe to call from tests directly.
  Future<void> initLocal() => _localService.initialize();

  /// Ensures the local store is open before a local-mode CRUD call. Cheap and
  /// idempotent — [LocalNoteService.initialize] returns early once opened.
  Future<void> _ensureLocalReady() async {
    if (!_localService.isInitialized) await _localService.initialize();
  }

  /// Enters local-only mode from the first-launch screen: the user opts to use
  /// NexaNote without a backend. Persists the choice, opens the local store,
  /// loads any existing on-device notes, and clears the backend-unavailable
  /// banner (this is a deliberate choice, not an error). The router then shows
  /// HomeScreen.
  Future<void> enableLocalMode() async {
    _localMode = true;
    _isBackendAvailable = false;
    _backendErrorMessage = null;
    _isLoading = false;
    try {
      final prefs = await SharedPreferences.getInstance();
      await prefs.setBool(kPrefsLocalMode, true);
    } catch (_) {
      // A failed prefs write is non-fatal; the in-memory flag still keeps the
      // current session local-first.
    }
    await _ensureLocalReady();
    await _loadLocalFallback();
    notifyListeners();
  }

  /// A successful connection means a backend is now configured: leave local
  /// mode so CRUD and sync use the backend again. Persisted so a later cold
  /// start does not silently fall back into local-only behaviour.
  Future<void> _clearLocalMode() async {
    if (!_localMode) return;
    _localMode = false;
    try {
      final prefs = await SharedPreferences.getInstance();
      await prefs.setBool(kPrefsLocalMode, false);
    } catch (_) {
      // Non-fatal; the in-memory flag is already cleared for this session.
    }
  }

  /// Best-effort upload of notes the user created while offline, run once when
  /// they leave local mode by connecting a backend. Reuses the existing
  /// [SyncService] push so the storage/sync layout is unchanged. Failures are
  /// swallowed: a connection must never be blocked by a migration hiccup — the
  /// notes stay in the local store and can be synced again later.
  Future<void> _migrateLocalNotesToBackend() async {
    try {
      final snapshot = await _localService.exportAllData();
      final hasLocal = snapshot.notebooks.isNotEmpty ||
          snapshot.notes.any((n) => !n.isDeleted);
      if (!hasLocal) return;
      final svc = SyncService(apiClient: client, local: _localService);
      await svc.pushLocal();
    } catch (_) {
      // Intentionally ignored — see doc comment.
    }
  }

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
      // A reachable backend means one is now configured — drop out of local
      // mode so CRUD and sync use the backend again.
      final wasLocalMode = _localMode;
      await _clearLocalMode();
      await _markEverConnected();
      try {
        if (wasLocalMode) {
          // The user took notes offline before configuring a server — upload
          // them so nothing created in local mode is lost on the switch.
          await _migrateLocalNotesToBackend();
        }
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

  /// Reloads [_notebooks] from the local SQLite store. Used by the local-mode
  /// CRUD paths so the UI reflects on-device data.
  Future<void> _refreshLocalNotebooks() async {
    await _ensureLocalReady();
    final nbs = await _localService.getNotebooks();
    _notebooks = nbs.map(_toApiNotebook).toList();
    _hasLocalData = _notebooks.isNotEmpty || _notes.isNotEmpty;
  }

  /// Reloads [_notes] from the local SQLite store, applying the same
  /// notebook/search filters the backend would. Used by the local-mode CRUD
  /// paths so list views stay in sync with on-device data.
  Future<void> _refreshLocalNotes({String? notebookId, String? search}) async {
    await _ensureLocalReady();
    final snapshot = await _localService.exportAllData();
    Iterable<local.Note> notes = snapshot.notes.where((n) => !n.isDeleted);
    if (notebookId != null) {
      notes = notes.where((n) => n.notebookId == notebookId);
    }
    final query = search?.trim().toLowerCase();
    if (query != null && query.isNotEmpty) {
      notes = notes.where((n) => n.title.toLowerCase().contains(query));
    }
    _notes = notes.map(_toApiNote).toList();
    _hasLocalData = _notebooks.isNotEmpty || _notes.isNotEmpty;
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

  /// Like [_toApiNote] but carries the locally-stored typed content and any
  /// saved ink [strokes] as a single page, matching the shape the editor
  /// expects from `client.getNote`. Lets the editor open notes — text *and*
  /// drawings — offline.
  api.Note _toApiNoteWithContent(
    local.Note n, {
    List<Stroke> strokes = const [],
  }) =>
      api.Note(
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
        pages: [
          api.NotePage(
            pageNumber: 1,
            template: 'blank',
            typedContent: n.typedContent,
            strokes: strokes.map(inkJsonFromStroke).toList(),
          ),
        ],
      );

  Future<void> loadNotebooks() async {
    if (_localMode) {
      await _refreshLocalNotebooks();
      notifyListeners();
      return;
    }
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
    if (_localMode) {
      await _ensureLocalReady();
      final nb = await _localService.createNotebook(name, color: color);
      final apiNb = _toApiNotebook(nb);
      _notebooks.insert(0, apiNb);
      _hasLocalData = true;
      notifyListeners();
      return apiNb;
    }
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
    if (_localMode) {
      await _ensureLocalReady();
      await _localService.hardDeleteNotebook(id);
      _notebooks.removeWhere((n) => n.id == id);
      if (_selectedNotebook?.id == id) { _selectedNotebook = null; _notes = []; }
      notifyListeners();
      return;
    }
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
    if (_localMode) {
      await _refreshLocalNotes(notebookId: notebookId, search: search);
      _isLoading = false;
      notifyListeners();
      return;
    }
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
    if (_localMode) {
      await _ensureLocalReady();
      final note = await _localService.createNote(
        title,
        notebookId: _selectedNotebook?.id,
        noteType: noteType,
      );
      final apiNote = _toApiNote(note);
      _notes.insert(0, apiNote);
      _hasLocalData = true;
      notifyListeners();
      return apiNote;
    }
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

  /// Fetches a single note with its page content, honouring the current mode:
  /// from the backend when connected, or from the local SQLite store in local
  /// mode. Screens call this instead of `client.getNote` directly so the
  /// editor opens offline.
  Future<api.Note> getNote(String id) async {
    if (_localMode) {
      await _ensureLocalReady();
      final n = await _localService.getNoteById(id);
      if (n == null) {
        throw StateError('Note not found in local store: $id');
      }
      final strokes = await _localService.getStrokesForNote(id);
      return _toApiNoteWithContent(n, strokes: strokes);
    }
    return client.getNote(id);
  }

  Future<void> deleteNote(String id) async {
    if (_localMode) {
      await _ensureLocalReady();
      await _localService.deleteNote(id);
      _notes.removeWhere((n) => n.id == id);
      if (_selectedNote?.id == id) _selectedNote = null;
      notifyListeners();
      return;
    }
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
    if (_localMode) {
      await _ensureLocalReady();
      final n = await _localService.getNoteById(id);
      if (n != null) {
        await _localService.upsertNote(n.copyWith(
          title: title,
          syncStatus: n.syncStatus == 'synced' ? 'modified' : n.syncStatus,
          updatedAt: DateTime.now().toUtc(),
        ));
      }
      await loadNotes(notebookId: _selectedNotebook?.id);
      return;
    }
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
    if (_localMode) {
      await _ensureLocalReady();
      final n = await _localService.getNoteById(noteId);
      if (n != null) {
        await _localService.upsertNote(n.copyWith(
          typedContent: content,
          syncStatus: n.syncStatus == 'synced' ? 'modified' : n.syncStatus,
          updatedAt: DateTime.now().toUtc(),
        ));
      }
      return;
    }
    try {
      await client.savePageText(noteId, pageNum, content);
      if (_markBackendAvailable()) notifyListeners();
    } catch (e) {
      await _handleBackendFailure(e);
      rethrow;
    }
  }

  /// Persists a page's ink [strokes], honouring the current mode. In local mode
  /// the drawing is written to the on-device SQLite store (replacing the note's
  /// previous strokes) and the note is flagged for later sync; with a backend
  /// configured it goes through the existing API. Throws on failure so the
  /// editor can surface a friendly error rather than silently dropping a
  /// drawing.
  ///
  /// [strokes] is the editor's wire shape (`{id,color,width,tool,points:[…]}`),
  /// the same payload the backend already accepts.
  Future<void> savePageInk(
    String noteId,
    int pageNum,
    List<Map<String, dynamic>> strokes,
  ) async {
    if (_localMode) {
      await _ensureLocalReady();
      // A single created-at instant per save, nudged forward per stroke so the
      // load order matches the draw order (getStrokesForNote sorts by it).
      final base = DateTime.now().toUtc();
      final localStrokes = <Stroke>[
        for (var i = 0; i < strokes.length; i++)
          strokeFromInkJson(
            noteId,
            strokes[i],
            base.add(Duration(milliseconds: i)),
          ),
      ];
      await _localService.replaceStrokesForNote(noteId, localStrokes);
      // Mark the note modified so a future sync uploads the drawing. The
      // note's content/type is untouched — only the sync bookkeeping changes.
      final n = await _localService.getNoteById(noteId);
      if (n != null) {
        await _localService.upsertNote(n.copyWith(
          syncStatus: n.syncStatus == 'synced' ? 'modified' : n.syncStatus,
          updatedAt: DateTime.now().toUtc(),
        ));
      }
      return;
    }
    try {
      await client.savePageInk(noteId, pageNum, strokes);
      if (_markBackendAvailable()) notifyListeners();
    } catch (e) {
      await _handleBackendFailure(e);
      rethrow;
    }
  }

  void selectNote(api.Note? note) { _selectedNote = note; notifyListeners(); }

  Future<String> triggerSync() async {
    if (_localMode) {
      // No backend configured — sync is a friendly no-op, not an error.
      _syncError = null;
      _syncMessage = kLocalModeSyncMessage;
      notifyListeners();
      return kLocalModeSyncMessage;
    }
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
    if (_localMode) {
      // No backend configured — auto-sync and manual sync are friendly no-ops.
      _syncError = null;
      _syncMessage = kLocalModeSyncMessage;
      notifyListeners();
      return const SyncResult(
        notebooksPushed: 0,
        notesPushed: 0,
        notebooksPulled: 0,
        notesPulled: 0,
      );
    }
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

/// Converts an editor/wire ink stroke (`{id,color,width,tool,points:[…]}`) into
/// the local [Stroke] model for SQLite persistence. The editor emits point
/// timestamps under `ts`; the local model stores them as `timestampMs`.
/// Tolerant of missing fields so a malformed stroke degrades gracefully rather
/// than throwing and losing the whole drawing.
Stroke strokeFromInkJson(
  String noteId,
  Map<String, dynamic> json,
  DateTime createdAt,
) {
  final rawPoints = (json['points'] as List?) ?? const [];
  return Stroke(
    id: (json['id'] as String?) ??
        DateTime.now().microsecondsSinceEpoch.toString(),
    noteId: noteId,
    color: (json['color'] as String?) ?? '#000000',
    width: (json['width'] as num?)?.toDouble() ?? 2.0,
    tool: (json['tool'] as String?) ?? 'pen',
    createdAt: createdAt,
    points: [
      for (final p in rawPoints)
        if (p is Map)
          StrokePoint(
            x: (p['x'] as num?)?.toDouble() ?? 0,
            y: (p['y'] as num?)?.toDouble() ?? 0,
            pressure: (p['pressure'] as num?)?.toDouble() ?? 0.5,
            timestampMs: (p['ts'] as num?)?.toInt() ?? 0,
          ),
    ],
  );
}

/// Inverse of [strokeFromInkJson]: renders a local [Stroke] back into the
/// editor/wire shape so the ink canvas can replay a saved drawing.
Map<String, dynamic> inkJsonFromStroke(Stroke stroke) => {
      'id': stroke.id,
      'color': stroke.color,
      'width': stroke.width,
      'tool': stroke.tool,
      'points': [
        for (final p in stroke.points)
          {
            'x': p.x,
            'y': p.y,
            'pressure': p.pressure,
            'ts': p.timestampMs,
          },
      ],
    };
