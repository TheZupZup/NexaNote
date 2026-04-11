import 'package:flutter/material.dart';
import 'package:shared_preferences/shared_preferences.dart';
import 'api_client.dart';

class AppState extends ChangeNotifier {
  String _apiUrl = 'http://127.0.0.1:8766';
  bool _isConnected = false;
  bool _isLoading = false;
  List<Notebook> _notebooks = [];
  List<Note> _notes = [];
  Notebook? _selectedNotebook;
  Note? _selectedNote;

  String get apiUrl => _apiUrl;
  bool get isConnected => _isConnected;
  bool get isLoading => _isLoading;
  List<Notebook> get notebooks => _notebooks;
  List<Note> get notes => _notes;
  Notebook? get selectedNotebook => _selectedNotebook;
  Note? get selectedNote => _selectedNote;
  ApiClient get client => ApiClient(baseUrl: _apiUrl);

  Future<void> init() async {
    final prefs = await SharedPreferences.getInstance();
    _apiUrl = prefs.getString('api_url') ?? 'http://127.0.0.1:8766';
    await connect();
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
}
