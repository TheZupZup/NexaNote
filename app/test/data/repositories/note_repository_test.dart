import 'package:flutter_test/flutter_test.dart';
import 'package:sqflite_common_ffi/sqflite_ffi.dart';

import 'package:nexanote/data/database/schema.dart';
import 'package:nexanote/data/repositories/note_repository.dart';
import 'package:nexanote/data/models/stroke.dart';
import 'package:nexanote/data/models/point.dart';
import 'package:nexanote/data/models/note.dart';

void main() {
  setUpAll(() {
    sqfliteFfiInit();
    databaseFactory = databaseFactoryFfi;
  });

  late dynamic db;
  late NoteRepository repo;

  setUp(() async {
    db = await openDatabase(
      inMemoryDatabasePath,
      version: Schema.version,
      onCreate: Schema.onCreate,
    );
    repo = NoteRepository(db);
  });

  tearDown(() async {
    await db.close();
  });

  // -----------------------------------------------------------------------
  // Notebooks
  // -----------------------------------------------------------------------

  group('createNotebook / getNotebooks', () {
    test('persists a notebook and returns it', () async {
      final nb = await repo.createNotebook('Work', color: '#ff0000');
      expect(nb.id, isNotEmpty);
      expect(nb.name, 'Work');
      expect(nb.color, '#ff0000');

      final all = await repo.getNotebooks();
      expect(all, hasLength(1));
      expect(all.first.name, 'Work');
    });

    test('returns notebooks sorted by name', () async {
      await repo.createNotebook('Zebra');
      await repo.createNotebook('Alpha');
      await repo.createNotebook('Middle');

      final all = await repo.getNotebooks();
      expect(all.map((n) => n.name).toList(), ['Alpha', 'Middle', 'Zebra']);
    });

    test('excludes archived notebooks by default', () async {
      final nb = await repo.createNotebook('Hidden');
      await db.update(
        'notebooks',
        {'is_archived': 1},
        where: 'id = ?',
        whereArgs: [nb.id],
      );

      final visible = await repo.getNotebooks();
      expect(visible, isEmpty);

      final all = await repo.getNotebooks(includeArchived: true);
      expect(all, hasLength(1));
    });
  });

  // -----------------------------------------------------------------------
  // Notes
  // -----------------------------------------------------------------------

  group('createNote / getNotesForNotebook', () {
    test('persists a note linked to a notebook', () async {
      final nb = await repo.createNotebook('NB');
      final note = await repo.createNote('My Note', notebookId: nb.id);

      expect(note.id, isNotEmpty);
      expect(note.title, 'My Note');
      expect(note.notebookId, nb.id);

      final notes = await repo.getNotesForNotebook(nb.id);
      expect(notes, hasLength(1));
      expect(notes.first.title, 'My Note');
    });

    test('returns notes newest first', () async {
      final nb = await repo.createNotebook('NB');
      await repo.createNote('First', notebookId: nb.id);
      // Ensure a distinct updated_at timestamp by nudging the DB directly.
      final second = await repo.createNote('Second', notebookId: nb.id);
      await db.update(
        'notes',
        {'updated_at': DateTime.now().toUtc().add(const Duration(seconds: 1)).toIso8601String()},
        where: 'id = ?',
        whereArgs: [second.id],
      );

      final notes = await repo.getNotesForNotebook(nb.id);
      expect(notes.first.title, 'Second');
    });

    test('excludes notes from other notebooks', () async {
      final nb1 = await repo.createNotebook('NB1');
      final nb2 = await repo.createNotebook('NB2');
      await repo.createNote('Note A', notebookId: nb1.id);
      await repo.createNote('Note B', notebookId: nb2.id);

      expect(await repo.getNotesForNotebook(nb1.id), hasLength(1));
      expect(await repo.getNotesForNotebook(nb2.id), hasLength(1));
    });
  });

  group('getNoteById', () {
    test('returns the correct note', () async {
      final created = await repo.createNote('Solo');
      final found = await repo.getNoteById(created.id);
      expect(found, isNotNull);
      expect(found!.id, created.id);
      expect(found.title, 'Solo');
    });

    test('returns null for unknown id', () async {
      final found = await repo.getNoteById('does-not-exist');
      expect(found, isNull);
    });
  });

  group('deleteNote', () {
    test('soft-deletes: note disappears from getNotesForNotebook', () async {
      final nb = await repo.createNotebook('NB');
      final note = await repo.createNote('Delete me', notebookId: nb.id);

      await repo.deleteNote(note.id);

      final notes = await repo.getNotesForNotebook(nb.id);
      expect(notes, isEmpty);
    });

    test('soft-delete sets is_deleted=1 and sync_status=modified in DB', () async {
      final note = await repo.createNote('Trash');
      await repo.deleteNote(note.id);

      final row = (await db.query('notes', where: 'id = ?', whereArgs: [note.id])).first;
      expect(row['is_deleted'], 1);
      expect(row['sync_status'], 'modified');
    });

    test('note is still retrievable by id after soft-delete', () async {
      final note = await repo.createNote('Soft');
      await repo.deleteNote(note.id);

      final found = await repo.getNoteById(note.id);
      expect(found, isNotNull);
      expect(found!.isDeleted, isTrue);
    });
  });

  // -----------------------------------------------------------------------
  // Strokes
  // -----------------------------------------------------------------------

  group('saveStroke / getStrokesForNote', () {
    test('persists a stroke with its points', () async {
      final note = await repo.createNote('Ink Note');
      final stroke = Stroke(
        id: 'stroke-a',
        noteId: note.id,
        color: '#0000ff',
        width: 3.0,
        tool: 'pen',
        createdAt: DateTime.utc(2024, 1, 1),
        points: [
          const StrokePoint(x: 10.0, y: 20.0, pressure: 0.8, timestampMs: 0),
          const StrokePoint(x: 15.0, y: 25.0, pressure: 0.9, timestampMs: 16),
        ],
      );

      await repo.saveStroke(stroke);

      final strokes = await repo.getStrokesForNote(note.id);
      expect(strokes, hasLength(1));

      final loaded = strokes.first;
      expect(loaded.id, 'stroke-a');
      expect(loaded.color, '#0000ff');
      expect(loaded.tool, 'pen');
      expect(loaded.points, hasLength(2));
      expect(loaded.points[0].x, 10.0);
      expect(loaded.points[1].pressure, 0.9);
    });

    test('points are returned in sequence order', () async {
      final note = await repo.createNote('Seq');
      final stroke = Stroke(
        id: 'stroke-b',
        noteId: note.id,
        createdAt: DateTime.utc(2024, 1, 1),
        points: [
          const StrokePoint(x: 1.0, y: 1.0, timestampMs: 0),
          const StrokePoint(x: 2.0, y: 2.0, timestampMs: 10),
          const StrokePoint(x: 3.0, y: 3.0, timestampMs: 20),
        ],
      );
      await repo.saveStroke(stroke);

      final loaded = (await repo.getStrokesForNote(note.id)).first;
      expect(loaded.points[0].x, 1.0);
      expect(loaded.points[1].x, 2.0);
      expect(loaded.points[2].x, 3.0);
    });

    test('re-saving a stroke replaces its points (no duplicates)', () async {
      final note = await repo.createNote('Upsert');
      final stroke = Stroke(
        id: 'stroke-c',
        noteId: note.id,
        createdAt: DateTime.utc(2024, 1, 1),
        points: [const StrokePoint(x: 1.0, y: 1.0)],
      );
      await repo.saveStroke(stroke);

      final updated = stroke.copyWith(
        points: [
          const StrokePoint(x: 5.0, y: 5.0),
          const StrokePoint(x: 6.0, y: 6.0),
        ],
      );
      await repo.saveStroke(updated);

      final loaded = (await repo.getStrokesForNote(note.id)).first;
      expect(loaded.points, hasLength(2));
      expect(loaded.points[0].x, 5.0);
    });

    test('returns empty list when note has no strokes', () async {
      final note = await repo.createNote('Empty');
      final strokes = await repo.getStrokesForNote(note.id);
      expect(strokes, isEmpty);
    });

    test('returns only strokes for the requested note', () async {
      final noteA = await repo.createNote('A');
      final noteB = await repo.createNote('B');

      await repo.saveStroke(Stroke(
        id: 'stroke-noteA',
        noteId: noteA.id,
        createdAt: DateTime.utc(2024, 1, 1),
      ));

      final strokesA = await repo.getStrokesForNote(noteA.id);
      final strokesB = await repo.getStrokesForNote(noteB.id);
      expect(strokesA, hasLength(1));
      expect(strokesB, isEmpty);
    });
  });

  // -----------------------------------------------------------------------
  // Edit revisions (local-first saves)
  // -----------------------------------------------------------------------

  group('edit revisions', () {
    Stroke stroke(String id, String noteId) => Stroke(
          id: id,
          noteId: noteId,
          createdAt: DateTime.utc(2024),
          points: const [StrokePoint(x: 1, y: 2)],
        );

    Note serverCopy(String id, {String title = 'Server', String text = ''}) {
      final at = DateTime.utc(2024, 1, 1);
      return Note(
        id: id,
        remoteId: id,
        title: title,
        typedContent: text,
        syncStatus: 'synced',
        createdAt: at,
        updatedAt: at,
      );
    }

    test('every edit bumps its own part only', () async {
      final note = await repo.createNote('A');
      await repo.updateNoteFields(note.id, {'title': 'B'}, NotePart.title);
      await repo.updateNoteFields(
          note.id, {'typed_content': 'x'}, NotePart.text);
      await repo.updateNoteFields(
          note.id, {'typed_content': 'xy'}, NotePart.text);
      final rev = await repo.recordInk(note.id, [stroke('s1', note.id)]);

      final stored = (await repo.getNoteById(note.id))!;
      expect(stored.titleRev, 1);
      expect(stored.textRev, 2);
      expect(stored.inkRev, 1);
      expect(rev, 1);
    });

    test('recording ink for a missing note writes nothing', () async {
      expect(await repo.recordInk('gone', [stroke('s1', 'gone')]), isNull);
      expect(await repo.getStrokesForNote('gone'), isEmpty);
    });

    test('a confirmation for an older revision leaves a newer edit pending',
        () async {
      final id = await repo.cacheRemoteNote(serverCopy('srv-1'), const []);
      await repo.updateNoteFields(id, {'typed_content': 'A'}, NotePart.text);
      await repo.updateNoteFields(id, {'typed_content': 'B'}, NotePart.text);

      // The save of A (revision 1) comes back after B was recorded.
      await repo.markPartSynced(id, NotePart.text, 1);
      var stored = (await repo.getNoteById(id))!;
      expect(stored.textPending, isTrue);
      expect(stored.syncStatus, 'modified');

      await repo.markPartSynced(id, NotePart.text, 2);
      // A late confirmation of A can't move it back.
      await repo.markPartSynced(id, NotePart.text, 1);
      stored = (await repo.getNoteById(id))!;
      expect(stored.textPending, isFalse);
      expect(stored.textSyncedRev, 2);
      expect(stored.syncStatus, 'synced');
    });

    test('caching the server copy keeps parts with a pending local edit',
        () async {
      final id = await repo.cacheRemoteNote(
          serverCopy('srv-1', title: 'T0', text: 'old'),
          [stroke('s0', 'srv-1')]);
      await repo.updateNoteFields(id, {'typed_content': 'mine'}, NotePart.text);
      await repo.recordInk(id, [stroke('s1', id), stroke('s2', id)]);

      await repo.cacheRemoteNote(
          serverCopy('srv-1', title: 'T1', text: 'theirs'),
          [stroke('s9', 'srv-1')]);

      final stored = (await repo.getNoteById(id))!;
      expect(stored.title, 'T1'); // nothing pending: refreshed
      expect(stored.typedContent, 'mine');
      expect((await repo.getStrokesForNote(id)).map((s) => s.id), ['s1', 's2']);
    });

    test('caching resolves a note uploaded from local mode to its row',
        () async {
      final note = await repo.createNote('Offline');
      await repo.setNoteRemoteId(note.id, 'srv-7', null);
      await repo.markNoteSyncedIfUnchanged(note.id, note.updatedAt);

      final id = await repo.cacheRemoteNote(serverCopy('srv-7'), const []);

      expect(id, note.id);
      expect(await repo.getAllNotes(), hasLength(1));
    });

    test('caching never overwrites a row that still has to be uploaded',
        () async {
      final note = await repo.createNote('Offline');
      await repo.updateNoteFields(
          note.id, {'typed_content': 'only copy'}, NotePart.text);
      await repo.setNoteRemoteId(note.id, 'srv-8', null); // still local_only
      await repo.replaceStrokesForNote(note.id, [stroke('s1', note.id)]);

      final id = await repo.cacheRemoteNote(
          serverCopy('srv-8', text: 'empty on the server'), const []);

      // It is the local copy of that note, left for pushLocal to finish:
      // no second row, and nothing of it is moved or replaced.
      expect(id, note.id);
      expect(await repo.getAllNotes(), hasLength(1));
      expect((await repo.getNoteById(note.id))!.typedContent, 'only copy');
      expect(await repo.getStrokesForNote(note.id), hasLength(1));
    });

    test('a stroke id used by another note never moves it over', () async {
      final a = await repo.createNote('A');
      final b = await repo.createNote('B');
      await repo.replaceStrokesForNote(a.id, [stroke('same', a.id)]);

      await repo.replaceStrokesForNote(b.id, [stroke('same', b.id)]);
      await repo.replaceStrokesForNote(b.id, [stroke('same', b.id)]);

      expect(await repo.getStrokesForNote(a.id), hasLength(1));
      expect(await repo.getStrokesForNote(b.id), hasLength(1));
      expect((await repo.getStrokesForNote(b.id)).single.points, hasLength(1));
    });

    test('text typed in local mode over a note without content becomes a '
        'note of its own', () async {
      final at = DateTime.utc(2024, 1, 1);
      await repo.upsertNote(Note(
        id: 'srv-3',
        remoteId: 'srv-3',
        title: 'Pulled',
        syncStatus: 'synced',
        hasContent: false,
        createdAt: at,
        updatedAt: at,
      ));
      await repo.updateNoteFields(
          'srv-3', {'typed_content': 'typed blind'}, NotePart.text, true);
      await repo.recordInk('srv-3', [stroke('s1', 'srv-3')], blind: true);

      await repo.cacheRemoteNote(
          serverCopy('srv-3', text: 'the real text'), [stroke('r1', 'srv-3')]);

      final original = (await repo.getNoteById('srv-3'))!;
      expect(original.typedContent, 'the real text');
      expect(original.hasPendingEdits, isFalse);
      expect((await repo.getStrokesForNote('srv-3')).map((s) => s.id), ['r1']);
      final copy =
          (await repo.getAllNotes()).singleWhere((n) => n.id != 'srv-3');
      expect(copy.title, 'Pulled (offline copy)');
      expect(copy.typedContent, 'typed blind');
      expect(copy.syncStatus, 'local_only');
      expect(await repo.getStrokesForNote(copy.id), hasLength(1));
    });

    test('a pull applied from an older snapshot keeps a pending edit',
        () async {
      final id = await repo.cacheRemoteNote(serverCopy('srv-1'), const []);
      final snapshot = (await repo.getNoteById(id))!;
      await repo.updateNoteFields(id, {'title': 'Mine'}, NotePart.title);

      await repo.applyPulledNote(
          id,
          snapshot.copyWith(
              title: 'Theirs', updatedAt: DateTime.utc(2030), isPinned: true));

      final stored = (await repo.getNoteById(id))!;
      expect(stored.title, 'Mine');
      expect(stored.titlePending, isTrue);
      expect(await repo.hardDeleteNoteIfSynced(id), 0);
      expect(await repo.getNotesWithPendingEdits(), hasLength(1));
    });
  });
}
