// lib/services/api_client.dart
// Parle avec le backend Python (API REST sur port 8766)

import 'dart:convert';
import 'package:http/http.dart' as http;

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
        title: j['title'],
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

class ApiClient {
  final String baseUrl;

  ApiClient({required this.baseUrl});

  Map<String, String> get _headers => {
        'Content-Type': 'application/json',
        'Accept': 'application/json',
      };

  // ----------------------------------------------------------------
  // Health
  // ----------------------------------------------------------------

  Future<bool> ping() async {
    try {
      final resp = await http
          .get(Uri.parse('$baseUrl/health'))
          .timeout(const Duration(seconds: 5));
      return resp.statusCode == 200;
    } catch (_) {
      return false;
    }
  }

  // ----------------------------------------------------------------
  // Notebooks
  // ----------------------------------------------------------------

  Future<List<Notebook>> getNotebooks() async {
    final resp = await http.get(
      Uri.parse('$baseUrl/notebooks'),
      headers: _headers,
    );
    if (resp.statusCode == 200) {
      final List data = jsonDecode(resp.body);
      return data.map((j) => Notebook.fromJson(j)).toList();
    }
    throw Exception('Failed to load notebooks: ${resp.statusCode}');
  }

  Future<Notebook> createNotebook({
    required String name,
    String color = '#6366f1',
  }) async {
    final resp = await http.post(
      Uri.parse('$baseUrl/notebooks'),
      headers: _headers,
      body: jsonEncode({'name': name, 'color': color}),
    );
    if (resp.statusCode == 201) {
      return Notebook.fromJson(jsonDecode(resp.body));
    }
    throw Exception('Failed to create notebook');
  }

  Future<void> deleteNotebook(String id) async {
    await http.delete(Uri.parse('$baseUrl/notebooks/$id'));
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

    final uri = Uri.parse('$baseUrl/notes').replace(queryParameters: params);
    final resp = await http.get(uri, headers: _headers);
    if (resp.statusCode == 200) {
      final List data = jsonDecode(resp.body);
      return data.map((j) => Note.fromJson(j)).toList();
    }
    throw Exception('Failed to load notes');
  }

  Future<Note> getNote(String id) async {
    final resp = await http.get(
      Uri.parse('$baseUrl/notes/$id?pages=true'),
      headers: _headers,
    );
    if (resp.statusCode == 200) {
      return Note.fromJson(jsonDecode(resp.body));
    }
    throw Exception('Failed to load note');
  }

  Future<Note> createNote({
    required String title,
    required String noteType,
    String? notebookId,
    String template = 'blank',
  }) async {
    final resp = await http.post(
      Uri.parse('$baseUrl/notes'),
      headers: _headers,
      body: jsonEncode({
        'title': title,
        'note_type': noteType,
        if (notebookId != null) 'notebook_id': notebookId,
        'template': template,
      }),
    );
    if (resp.statusCode == 201) {
      return Note.fromJson(jsonDecode(resp.body));
    }
    throw Exception('Failed to create note');
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

    final resp = await http.put(
      Uri.parse('$baseUrl/notes/$id'),
      headers: _headers,
      body: jsonEncode(body),
    );
    if (resp.statusCode == 200) {
      return Note.fromJson(jsonDecode(resp.body));
    }
    throw Exception('Failed to update note');
  }

  Future<void> deleteNote(String id) async {
    await http.delete(Uri.parse('$baseUrl/notes/$id'));
  }

  Future<void> restoreNote(String id) async {
    await http.post(Uri.parse('$baseUrl/notes/$id/restore'));
  }

  // ----------------------------------------------------------------
  // Page text content
  // ----------------------------------------------------------------

  Future<void> savePageText(String noteId, int pageNum, String content) async {
    await http.put(
      Uri.parse('$baseUrl/notes/$noteId/pages/$pageNum/text'),
      headers: _headers,
      body: jsonEncode({'typed_content': content}),
    );
  }

  Future<void> savePageInk(String noteId, int pageNum, List<Map<String, dynamic>> strokes) async {
    await http.put(
      Uri.parse('$baseUrl/notes/$noteId/pages/$pageNum/ink'),
      headers: _headers,
      body: jsonEncode({'strokes': strokes}),
    );
  }

  // ----------------------------------------------------------------
  // Sync
  // ----------------------------------------------------------------

  Future<Map<String, dynamic>> triggerSync() async {
    final resp = await http.post(
      Uri.parse('$baseUrl/sync/trigger'),
      headers: _headers,
    );
    if (resp.statusCode == 200) {
      return jsonDecode(resp.body);
    }
    throw Exception('Sync failed: ${resp.body}');
  }

  Future<Map<String, dynamic>> getSyncStatus() async {
    final resp = await http.get(Uri.parse('$baseUrl/sync/status'));
    if (resp.statusCode == 200) return jsonDecode(resp.body);
    return {'status': 'unknown'};
  }

  Future<void> configureSync({
    required String serverUrl,
    required String username,
    required String password,
  }) async {
    await http.post(
      Uri.parse('$baseUrl/sync/configure'),
      headers: _headers,
      body: jsonEncode({
        'server_url': serverUrl,
        'username': username,
        'password': password,
      }),
    );
  }

  // ----------------------------------------------------------------
  // Stats
  // ----------------------------------------------------------------

  Future<Map<String, dynamic>> getStats() async {
    final resp = await http.get(Uri.parse('$baseUrl/stats'));
    if (resp.statusCode == 200) return jsonDecode(resp.body);
    return {};
  }
}
