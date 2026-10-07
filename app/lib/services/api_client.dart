// lib/services/api_client.dart
// Parle avec le backend Python (API REST sur port 8766)

import 'dart:async';
import 'dart:convert';
import 'package:http/http.dart' as http;

import 'title_cleaner.dart';

class Notebook {
  final String id;
  final String name;
  final String color;
  final String icon;
  final String updatedAt;

  Notebook({
    required this.id,
    required this.name,
    required this.color,
    required this.icon,
    required this.updatedAt,
  });

  factory Notebook.fromJson(Map<String, dynamic> j) => Notebook(
        id: j['id'],
        name: j['name'],
        color: j['color'] ?? '#6366f1',
        icon: j['icon'] ?? 'notebook',
        updatedAt: j['updated_at'] ?? '',
      );
}

class Note {
  final String id;
  final String title;
  final String noteType;
  final String? notebookId;
  final List<String> tags;
  final bool isPinned;
  final bool isDeleted;
  final int pageCount;
  final String updatedAt;
  final String createdAt;
  final List<NotePage>? pages;

  Note({
    required this.id,
    required this.title,
    required this.noteType,
    this.notebookId,
    required this.tags,
    required this.isPinned,
    required this.isDeleted,
    required this.pageCount,
    required this.updatedAt,
    required this.createdAt,
    this.pages,
  });

  factory Note.fromJson(Map<String, dynamic> j) => Note(
        id: j['id'],
        // Strip slug/extension artifacts so list views and the editor
        // never display things like `Foo__Md.Q2Hhd`. The server side keeps
        // the raw title for storage; cleanup is a presentation concern.
        title: cleanRemoteTitle((j['title'] as String?) ?? ''),
        noteType: j['note_type'] ?? 'typed',
        notebookId: j['notebook_id'],
        tags: List<String>.from(j['tags'] ?? []),
        isPinned: j['is_pinned'] ?? false,
        isDeleted: j['is_deleted'] ?? false,
        pageCount: j['page_count'] ?? 0,
        updatedAt: j['updated_at'] ?? '',
        createdAt: j['created_at'] ?? '',
        pages: j['pages'] != null
            ? (j['pages'] as List).map((p) => NotePage.fromJson(p)).toList()
            : null,
      );

  Note copyWith({
    String? title,
    String? noteType,
    String? notebookId,
    List<String>? tags,
    bool? isPinned,
    bool? isDeleted,
    int? pageCount,
    String? updatedAt,
    String? createdAt,
    List<NotePage>? pages,
  }) {
    return Note(
      id: id,
      title: title ?? this.title,
      noteType: noteType ?? this.noteType,
      notebookId: notebookId ?? this.notebookId,
      tags: tags ?? this.tags,
      isPinned: isPinned ?? this.isPinned,
      isDeleted: isDeleted ?? this.isDeleted,
      pageCount: pageCount ?? this.pageCount,
      updatedAt: updatedAt ?? this.updatedAt,
      createdAt: createdAt ?? this.createdAt,
      pages: pages ?? this.pages,
    );
  }
}

class NotePage {
  final int pageNumber;
  final String template;
  final String typedContent;
  final List<dynamic> strokes;

  NotePage({
    required this.pageNumber,
    required this.template,
    required this.typedContent,
    required this.strokes,
  });

  factory NotePage.fromJson(Map<String, dynamic> j) => NotePage(
        pageNumber: j['page_number'] ?? 1,
        template: j['template'] ?? 'blank',
        typedContent: j['typed_content'] ?? '',
        strokes: j['strokes'] ?? [],
      );
}

/// A backend call that did not succeed: unexpected HTTP status, or a request
/// that took longer than its timeout.
class ApiException implements Exception {
  final String message;
  final int? statusCode;
  const ApiException(this.message, {this.statusCode});

  @override
  String toString() =>
      statusCode == null ? message : '$message (HTTP $statusCode)';
}

class ApiClient {
  final String baseUrl;
  final http.Client? _httpClient;

  /// Upper bound for ordinary requests. Without one, a server that accepts
  /// the connection and never answers leaves a save "Saving…" forever.
  static const Duration requestTimeout = Duration(seconds: 20);

  /// Saves carry the whole page (a long drawing can be megabytes), so on a
  /// slow mobile uplink they get more time than ordinary requests.
  static const Duration uploadTimeout = Duration(minutes: 2);

  /// `/sync/trigger` runs a whole WebDAV sync before answering.
  static const Duration syncTimeout = Duration(minutes: 5);

  ApiClient({required this.baseUrl, http.Client? httpClient})
      : _httpClient = httpClient;

  Future<http.Response> _send(
    String what,
    Future<http.Response> Function(http.Client client) request, {
    Duration timeout = requestTimeout,
  }) async {
    final injected = _httpClient;
    final client = injected ?? http.Client();
    try {
      return await request(client).timeout(timeout);
    } on TimeoutException {
      throw ApiException('$what timed out after ${timeout.inSeconds}s');
    } finally {
      if (injected == null) client.close();
    }
  }

  /// Throws unless [resp] has one of the [expected] status codes. Write calls
  /// must not report success when the server refused the change.
  static void _expect(http.Response resp, String what,
      [List<int> expected = const [200]]) {
    if (!expected.contains(resp.statusCode)) {
      throw ApiException(what, statusCode: resp.statusCode);
    }
  }

  Map<String, String> get _headers => {
        'Content-Type': 'application/json',
        'Accept': 'application/json',
      };

  // ----------------------------------------------------------------
  // Health
  // ----------------------------------------------------------------

  Future<bool> ping() async {
    try {
      final resp = await _send('Ping',
          (c) => c.get(Uri.parse('$baseUrl/health')),
          timeout: const Duration(seconds: 5));
      return resp.statusCode == 200;
    } catch (_) {
      return false;
    }
  }

  // ----------------------------------------------------------------
  // Notebooks
  // ----------------------------------------------------------------

  Future<List<Notebook>> getNotebooks() async {
    const what = 'Failed to load notebooks';
    final resp = await _send(
        what, (c) => c.get(Uri.parse('$baseUrl/notebooks'), headers: _headers));
    _expect(resp, what);
    final List data = jsonDecode(resp.body);
    return data.map((j) => Notebook.fromJson(j)).toList();
  }

  Future<Notebook> createNotebook({
    required String name,
    String color = '#6366f1',
  }) async {
    const what = 'Failed to create notebook';
    final resp = await _send(
        what,
        (c) => c.post(
              Uri.parse('$baseUrl/notebooks'),
              headers: _headers,
              body: jsonEncode({'name': name, 'color': color}),
            ));
    _expect(resp, what, const [201]);
    return Notebook.fromJson(jsonDecode(resp.body));
  }

  Future<void> deleteNotebook(String id) async {
    const what = 'Failed to delete notebook';
    final resp = await _send(
        what, (c) => c.delete(Uri.parse('$baseUrl/notebooks/$id')));
    // 404: already gone (deleted from another device), which is the goal.
    _expect(resp, what, const [200, 204, 404]);
  }

  // ----------------------------------------------------------------
  // Notes
  // ----------------------------------------------------------------

  Future<List<Note>> getNotes({
    String? notebookId,
    String? search,
    bool includeDeleted = false,
  }) async {
    final params = <String, String>{};
    if (notebookId != null) params['notebook_id'] = notebookId;
    if (search != null && search.isNotEmpty) params['q'] = search;
    if (includeDeleted) params['include_deleted'] = 'true';

    const what = 'Failed to load notes';
    final uri = Uri.parse('$baseUrl/notes').replace(queryParameters: params);
    final resp = await _send(what, (c) => c.get(uri, headers: _headers));
    _expect(resp, what);
    final List data = jsonDecode(resp.body);
    return data.map((j) => Note.fromJson(j)).toList();
  }

  Future<Note> getNote(String id) async {
    const what = 'Failed to load note';
    final resp = await _send(
        what,
        (c) =>
            c.get(Uri.parse('$baseUrl/notes/$id?pages=true'), headers: _headers));
    _expect(resp, what);
    return Note.fromJson(jsonDecode(resp.body));
  }

  Future<Note> createNote({
    required String title,
    required String noteType,
    String? notebookId,
    String template = 'blank',
  }) async {
    const what = 'Failed to create note';
    final resp = await _send(
        what,
        (c) => c.post(
              Uri.parse('$baseUrl/notes'),
              headers: _headers,
              body: jsonEncode({
                'title': title,
                'note_type': noteType,
                if (notebookId != null) 'notebook_id': notebookId,
                'template': template,
              }),
            ));
    _expect(resp, what, const [201]);
    return Note.fromJson(jsonDecode(resp.body));
  }

  Future<Note> updateNote(
    String id, {
    String? title,
    bool? isPinned,
    List<String>? tags,
  }) async {
    final body = <String, dynamic>{};
    if (title != null) body['title'] = title;
    if (isPinned != null) body['is_pinned'] = isPinned;
    if (tags != null) body['tags'] = tags;

    const what = 'Failed to update note';
    final resp = await _send(
        what,
        (c) => c.put(
              Uri.parse('$baseUrl/notes/$id'),
              headers: _headers,
              body: jsonEncode(body),
            ));
    _expect(resp, what);
    return Note.fromJson(jsonDecode(resp.body));
  }

  Future<void> deleteNote(String id) async {
    const what = 'Failed to delete note';
    final resp =
        await _send(what, (c) => c.delete(Uri.parse('$baseUrl/notes/$id')));
    // 404: already gone (deleted from another device), which is the goal.
    _expect(resp, what, const [200, 204, 404]);
  }

  Future<void> restoreNote(String id) async {
    const what = 'Failed to restore note';
    final resp = await _send(
        what, (c) => c.post(Uri.parse('$baseUrl/notes/$id/restore')));
    _expect(resp, what);
  }

  // ----------------------------------------------------------------
  // Page text content
  // ----------------------------------------------------------------

  Future<void> savePageText(String noteId, int pageNum, String content) async {
    const what = 'Failed to save text';
    final resp = await _send(
        what,
        (c) => c.put(
              Uri.parse('$baseUrl/notes/$noteId/pages/$pageNum/text'),
              headers: _headers,
              body: jsonEncode({'typed_content': content}),
            ),
        timeout: uploadTimeout);
    _expect(resp, what);
  }

  Future<void> savePageInk(
      String noteId, int pageNum, List<Map<String, dynamic>> strokes) async {
    const what = 'Failed to save drawing';
    final resp = await _send(
        what,
        (c) => c.put(
              Uri.parse('$baseUrl/notes/$noteId/pages/$pageNum/ink'),
              headers: _headers,
              body: jsonEncode({'strokes': strokes}),
            ),
        timeout: uploadTimeout);
    _expect(resp, what);
  }

  // ----------------------------------------------------------------
  // Sync
  // ----------------------------------------------------------------

  Future<Map<String, dynamic>> triggerSync() async {
    final resp = await _send(
        'Sync',
        (c) => c.post(Uri.parse('$baseUrl/sync/trigger'), headers: _headers),
        timeout: syncTimeout);
    if (resp.statusCode == 200) {
      return jsonDecode(resp.body);
    }
    throw Exception('Sync failed: ${resp.body}');
  }

  Future<Map<String, dynamic>> getSyncStatus() async {
    final resp = await _send(
        'Sync status', (c) => c.get(Uri.parse('$baseUrl/sync/status')));
    if (resp.statusCode == 200) return jsonDecode(resp.body);
    return {'status': 'unknown'};
  }

  Future<void> configureSync({
    required String serverUrl,
    required String username,
    required String password,
  }) async {
    const what = 'Failed to configure sync';
    final resp = await _send(
        what,
        (c) => c.post(
              Uri.parse('$baseUrl/sync/configure'),
              headers: _headers,
              body: jsonEncode({
                'server_url': serverUrl,
                'username': username,
                'password': password,
              }),
            ));
    _expect(resp, what);
  }

  // ----------------------------------------------------------------
  // Stats
  // ----------------------------------------------------------------

  Future<Map<String, dynamic>> getStats() async {
    final resp =
        await _send('Stats', (c) => c.get(Uri.parse('$baseUrl/stats')));
    if (resp.statusCode == 200) return jsonDecode(resp.body);
    return {};
  }

  Future<Map<String, dynamic>> getStorageInfo() async {
    final resp =
        await _send('Storage info', (c) => c.get(Uri.parse('$baseUrl/storage')));
    if (resp.statusCode == 200) return jsonDecode(resp.body);
    return {};
  }
}
