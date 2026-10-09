import 'dart:async';

import 'package:nexanote/services/api_client.dart' as api;

/// What the NexaNote backend stores, shared by every [FakeBackendClient].
/// Each simulated app process gets its own client, so a "killed" process
/// can be cut off without touching the server or the next process.
class FakeServer {
  final notes = <String, api.Note>{};
  final text = <String, String>{};
  final ink = <String, List<Map<String, dynamic>>>{};
  var _ids = 0;
  var _clock = DateTime.utc(2024, 1, 1);

  /// Every page save that reached the server, in order: `text:<content>` or
  /// `ink:<stroke count>`.
  final writes = <String>[];

  String _tick() {
    _clock = _clock.add(const Duration(seconds: 1));
    return _clock.toIso8601String();
  }

  api.Note create(String title, String noteType) {
    final now = _tick();
    final note = api.Note(
      id: 'srv-${++_ids}',
      title: title,
      noteType: noteType,
      tags: const [],
      isPinned: false,
      isDeleted: false,
      pageCount: 1,
      updatedAt: now,
      createdAt: now,
    );
    notes[note.id] = note;
    return note;
  }

  String touch(String id, {String? title}) {
    final now = _tick();
    notes[id] = notes[id]!.copyWith(title: title, updatedAt: now);
    return now;
  }

  api.Note full(String id) => notes[id]!.copyWith(pages: [
        api.NotePage(
          pageNumber: 1,
          template: 'blank',
          typedContent: text[id] ?? '',
          strokes: [...?ink[id]],
        ),
      ]);
}

/// [api.ApiClient] backed by a [FakeServer]. [down] makes every call fail
/// like an unreachable backend; [hold] keeps page saves waiting until
/// [release] (or forever).
class FakeBackendClient extends api.ApiClient {
  FakeBackendClient(this.server) : super(baseUrl: 'http://fake.test');

  final FakeServer server;
  bool down = false;
  bool hold = false;

  /// Drawing saves fail while text and title saves go through.
  bool failInk = false;

  /// Text saves are refused with this HTTP status (say 413).
  int? refuseText;
  final _held = <Completer<void>>[];

  int get heldCount => _held.length;

  void release() => _held.removeAt(0).complete();

  static const _unreachable =
      api.ApiException('Backend unreachable', statusCode: 503);

  Future<void> _call() async {
    if (down) throw _unreachable;
  }

  Future<void> _write(String id) async {
    await _call();
    if (hold) {
      final gate = Completer<void>();
      _held.add(gate);
      await gate.future;
    }
    if (down) throw _unreachable;
    if (!server.notes.containsKey(id)) {
      throw const api.ApiException('Note not found', statusCode: 404);
    }
  }

  @override
  Future<bool> ping() async {
    await _call();
    return true;
  }

  @override
  Future<List<api.Notebook>> getNotebooks() async {
    await _call();
    return const [];
  }

  @override
  Future<List<api.Note>> getNotes({
    String? notebookId,
    String? search,
    bool includeDeleted = false,
  }) async {
    await _call();
    return server.notes.values.toList();
  }

  @override
  Future<api.Note> createNote({
    required String title,
    required String noteType,
    String? notebookId,
    String template = 'blank',
  }) async {
    await _call();
    return server.create(title, noteType);
  }

  /// Note reads answer with the server state at request time, but only
  /// once [releaseRead] is called, like a response still on the wire.
  bool holdReads = false;
  final _heldReads = <Completer<void>>[];

  void releaseRead() => _heldReads.removeAt(0).complete();

  @override
  Future<api.Note> getNote(String id) async {
    await _call();
    if (!server.notes.containsKey(id)) {
      throw const api.ApiException('Note not found', statusCode: 404);
    }
    final snapshot = server.full(id);
    if (holdReads) {
      final gate = Completer<void>();
      _heldReads.add(gate);
      await gate.future;
    }
    return snapshot;
  }

  @override
  Future<void> deleteNote(String id) async {
    await _call();
    server.notes.remove(id);
    server.text.remove(id);
    server.ink.remove(id);
  }

  @override
  Future<api.Note> updateNote(String id,
      {String? title, bool? isPinned, List<String>? tags}) async {
    await _write(id);
    server.touch(id, title: title);
    server.writes.add('title:$title');
    return server.notes[id]!;
  }

  @override
  Future<String?> savePageText(
      String noteId, int pageNum, String content) async {
    await _write(noteId);
    final status = refuseText;
    if (status != null) {
      throw api.ApiException('Failed to save text', statusCode: status);
    }
    server.text[noteId] = content;
    server.writes.add('text:$content');
    return server.touch(noteId);
  }

  @override
  Future<String?> savePageInk(
      String noteId, int pageNum, List<Map<String, dynamic>> strokes) async {
    await _write(noteId);
    if (failInk) throw _unreachable;
    server.ink[noteId] = strokes;
    server.writes.add('ink:${strokes.length}');
    return server.touch(noteId);
  }
}
