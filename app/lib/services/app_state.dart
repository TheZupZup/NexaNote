import 'package:flutter/material.dart';
import 'package:shared_preferences/shared_preferences.dart';

import '../data/models/note.dart' as local;
import '../data/models/notebook.dart' as local;
import '../data/models/stroke.dart';
import 'api_client.dart';
import 'local_note_service.dart';

class AppState extends ChangeNotifier {
  final LocalNoteService _localService;

  String _apiUrl = 'http://127.0.0.1:8766';
  bool _isConnected = false;
  bool _isLoading = false;
  List<Notebook> _notebooks = [];
  List<Note> _notes = [];
  Notebook? _selectedNotebook;
  Note? _selectedNote;

  // Local-only state, sourced from the on-device SQLite store.
  List<local.Notebook> _localNotebooks = [];
  List<local.Note> _localNotes = [];

  AppState({LocalNoteService? localService})
      : _localService = localService ?? LocalNoteService();

  String get apiUrl => _apiUrl;
  bool get isConnected => _isConnected;
  bool get isLoading => _isLoading;
  List<Notebook> get notebooks => _notebooks;
  List<Note> get notes => _notes;
  Notebook? get selectedNotebook => _selectedNotebook;
  Note? get selectedNote => _selectedNote;
  ApiClient get client => ApiClient(baseUrl: _apiUrl);

  LocalNoteService get localService => _localService;
  List<local.Notebook> get localNotebooks => List.unmodifiable(_localNotebooks);
  List<local.Note> get localNotes => List.unmodifiable(_localNotes);

  Future<void> init() async {
    await initLocal();
    final prefs = await SharedPreferences.getInstance();
    _apiUrl = prefs.getString('api_url') ?? 'http://127.0.0.1:8766';
    await connect();
  }

  /// Opens the local DB and loads any locally stored notebooks. Safe to call
  /// from tests or from [init] in production.
  Future<void> initLocal() async {
    await _localService.initialize();
    await loadLocalNotebooks();
  }

  Future<void> connect({String? url}) async {
    if (url != null) {
      _apiUrl = url;
      final prefs = await SharedPreferences.getInstance();
      await prefs.setString('api_url', _apiUrl);
    }
    _isLoading = true;
    notifyListeners();
    try {
      _isConnected = await client.ping();
      if (_isConnected) { await loadNotebooks(); await loadNotes(); }
    } catch (_) { _isConnected = false; }
    _isLoading = false;
    notifyListeners();
  }

  Future<void> loadNotebooks() async {
    _notebooks = await client.getNotebooks();
    notifyListeners();
  }

  Future<Notebook> createNotebook(String name, String color) async {
    final nb = await client.createNotebook(name: name, color: color);
    _notebooks.insert(0, nb);
    notifyListeners();
    return nb;
  }

  Future<void> deleteNotebook(String id) async {
    await client.deleteNotebook(id);
    _notebooks.removeWhere((n) => n.id == id);
    if (_selectedNotebook?.id == id) { _selectedNotebook = null; _notes = []; }
    notifyListeners();
  }

  void selectNotebook(Notebook? nb) {
    _selectedNotebook = nb;
    notifyListeners();
    loadNotes(notebookId: nb?.id);
  }

  Future<void> loadNotes({String? notebookId, String? search}) async {
    _isLoading = true;
    notifyListeners();
    try { _notes = await client.getNotes(notebookId: notebookId, search: search); }
    catch (_) {}
    _isLoading = false;
    notifyListeners();
  }

  Future<Note> createNote({required String title, required String noteType, String template = 'blank'}) async {
    final note = await client.createNote(
      title: title, noteType: noteType,
      notebookId: _selectedNotebook?.id, template: template);
    _notes.insert(0, note);
    notifyListeners();
    return note;
  }

  Future<void> deleteNote(String id) async {
    await client.deleteNote(id);
    _notes.removeWhere((n) => n.id == id);
    if (_selectedNote?.id == id) _selectedNote = null;
    notifyListeners();
  }

  Future<void> updateNoteTitle(String id, String title) async {
    await client.updateNote(id, title: title);
    await loadNotes(notebookId: _selectedNotebook?.id);
  }

  Future<void> savePageText(String noteId, int pageNum, String content) async {
    await client.savePageText(noteId, pageNum, content);
  }

  void selectNote(Note? note) { _selectedNote = note; notifyListeners(); }

  Future<String> triggerSync() async {
    try {
      final result = await client.triggerSync();
      return result['summary'] ?? 'Sync complete';
    } catch (e) { return 'Sync failed: $e'; }
  }

  // -----------------------------------------------------------------------
  // Local-only operations (offline-first store)
  // -----------------------------------------------------------------------

  Future<void> loadLocalNotebooks() async {
    _localNotebooks = await _localService.getNotebooks();
    notifyListeners();
  }

  Future<local.Notebook> createLocalNotebook(
    String name, {
    String color = '#6366f1',
  }) async {
    final nb = await _localService.createNotebook(name, color: color);
    _localNotebooks = [..._localNotebooks, nb];
    notifyListeners();
    return nb;
  }

  Future<void> loadLocalNotesForNotebook(String notebookId) async {
    _localNotes = await _localService.getNotesForNotebook(notebookId);
    notifyListeners();
  }

  Future<local.Note> createLocalNote(
    String title, {
    String? notebookId,
    String noteType = 'typed',
  }) async {
    final note = await _localService.createNote(
      title,
      notebookId: notebookId,
      noteType: noteType,
    );
    _localNotes = [note, ..._localNotes];
    notifyListeners();
    return note;
  }

  Future<local.Note?> getLocalNoteById(String id) =>
      _localService.getNoteById(id);

  Future<void> saveStroke(Stroke stroke) => _localService.saveStroke(stroke);

  Future<List<Stroke>> getStrokesForNote(String noteId) =>
      _localService.getStrokesForNote(noteId);
}
