import 'package:flutter/material.dart';
import 'package:provider/provider.dart';
import '../services/app_state.dart';
import '../services/api_client.dart';
import '../widgets/notebook_sidebar.dart';
import '../widgets/notes_list.dart';
import 'note_editor_screen.dart';
import 'settings_screen.dart';

class HomeScreen extends StatefulWidget {
  const HomeScreen({super.key});
  @override
  State<HomeScreen> createState() => _HomeScreenState();
}

class _HomeScreenState extends State<HomeScreen> {
  @override
  Widget build(BuildContext context) {
    final isWide = MediaQuery.of(context).size.width > 800;
    return isWide ? const _DesktopLayout() : const _MobileLayout();
  }
}

class _DesktopLayout extends StatelessWidget {
  const _DesktopLayout();

  @override
  Widget build(BuildContext context) {
    final state = context.watch<AppState>();
    final scheme = Theme.of(context).colorScheme;

    return Scaffold(
      body: Column(children: [
        // Only when a *configured* backend is unreachable — never in local
        // mode, where running without a backend is the user's choice, not an
        // error.
        if (!state.isBackendAvailable && !state.localMode)
          _OfflineBanner(message: state.backendErrorMessage),
        Expanded(child: Row(children: [
        SizedBox(width: 240, child: NotebookSidebar(
          notebooks: state.notebooks,
          selected: state.selectedNotebook,
          onSelect: state.selectNotebook,
          onCreate: () => _createNotebook(context),
          onSync: () => _sync(context),
          isSyncing: state.isSyncing,
          hasSyncError: state.syncError != null,
          syncError: state.syncError,
          lastSyncTime: state.lastSyncTime,
        )),
        VerticalDivider(width: 1, color: scheme.outlineVariant),
        SizedBox(width: 300, child: Column(children: [
          _NotesHeader(
            title: state.selectedNotebook?.name ?? 'All Notes',
            onSearch: (q) => state.loadNotes(notebookId: state.selectedNotebook?.id, search: q),
            onNewNote: () => _createNote(context),
          ),
          Expanded(child: NotesList(
            notes: state.notes,
            selected: state.selectedNote,
            isLoading: state.isLoading,
            onSelect: (note) => _openNote(context, note.id),
            onDelete: (note) => state.deleteNote(note.id),
          )),
        ])),
        VerticalDivider(width: 1, color: scheme.outlineVariant),
        Expanded(child: state.selectedNote != null
          // Keyed by note id: without it Flutter reuses the editor State
          // and keeps showing (and saving to) the previously opened note.
          ? NoteEditorScreen(
              key: ValueKey(state.selectedNote!.id),
              note: state.selectedNote!)
          : _EmptyEditor()),
      ])),
      ]),
    );
  }

  Future<void> _createNotebook(BuildContext context) async {
    final name = await _inputDialog(context, 'New Notebook', 'Name');
    if (name != null && name.isNotEmpty) {
      await context.read<AppState>().createNotebook(name, '#6366f1');
    }
  }

  Future<void> _createNote(BuildContext context) async {
    final state = context.read<AppState>();
    final note = await state.createNote(title: 'Untitled', noteType: 'typed');
    if (context.mounted) await _openNote(context, note.id);
  }

  Future<void> _sync(BuildContext context) async {
    final state = context.read<AppState>();
    await state.triggerSync();
    if (context.mounted) _showSyncResult(context, state);
  }
}

/// Opens note [id] and returns it, or null when it is stale (another note was
/// picked meanwhile) or could not be loaded; the error is shown to the user
/// instead of the tap silently doing nothing.
Future<Note?> _openNote(BuildContext context, String id) async {
  try {
    return await context.read<AppState>().openNote(id);
  } catch (e) {
    if (context.mounted) {
      ScaffoldMessenger.of(context).showSnackBar(SnackBar(
        content: Text('Could not open note: $e'),
        backgroundColor: Colors.red.shade700,
      ));
    }
    return null;
  }
}

void _showSyncResult(BuildContext context, AppState state) {
  final error = state.syncError;
  final message = error ?? state.syncMessage ?? 'Sync complete';
  ScaffoldMessenger.of(context).showSnackBar(SnackBar(
    content: Text(message),
    backgroundColor: error != null ? Colors.red.shade700 : null,
    duration: const Duration(seconds: 3),
  ));
}

class _MobileLayout extends StatelessWidget {
  const _MobileLayout();

  @override
  Widget build(BuildContext context) {
    final state = context.watch<AppState>();

    return Scaffold(
      appBar: AppBar(
        title: Text(state.selectedNotebook?.name ?? 'NexaNote'),
        actions: [
          IconButton(
            icon: state.isSyncing
                ? const SizedBox(
                    width: 18,
                    height: 18,
                    child: CircularProgressIndicator(
                        strokeWidth: 2, color: Colors.white))
                : Icon(state.syncError != null
                    ? Icons.sync_problem
                    : Icons.sync),
            onPressed: state.isSyncing
                ? null
                : () async {
                    await state.triggerSync();
                    if (context.mounted) _showSyncResult(context, state);
                  },
          ),
        ],
      ),
      body: Column(children: [
        if (!state.isBackendAvailable && !state.localMode)
          _OfflineBanner(message: state.backendErrorMessage),
        Expanded(child: NotesList(
          notes: state.notes,
          selected: state.selectedNote,
          isLoading: state.isLoading,
          onSelect: (note) async {
            final full = await _openNote(context, note.id);
            if (full != null && context.mounted) {
              Navigator.push(context, MaterialPageRoute(
                builder: (_) => ChangeNotifierProvider.value(
                  value: state,
                  child: NoteEditorScreen(note: full))));
            }
          },
          onDelete: (note) => state.deleteNote(note.id),
        )),
      ]),
      drawer: Drawer(child: NotebookSidebar(
        notebooks: state.notebooks,
        selected: state.selectedNotebook,
        onSelect: (nb) { state.selectNotebook(nb); Navigator.pop(context); },
        onCreate: () async {
          final name = await _inputDialog(context, 'New Notebook', 'Name');
          if (name != null) await state.createNotebook(name, '#6366f1');
        },
        onSync: () async {
          await state.triggerSync();
          if (context.mounted) _showSyncResult(context, state);
        },
        isSyncing: state.isSyncing,
        hasSyncError: state.syncError != null,
        syncError: state.syncError,
        lastSyncTime: state.lastSyncTime,
      )),
      floatingActionButton: FloatingActionButton(
        backgroundColor: const Color(0xFF6366F1),
        foregroundColor: Colors.white,
        onPressed: () async {
          final note = await state.createNote(title: 'Untitled', noteType: 'typed');
          if (!context.mounted) return;
          final full = await _openNote(context, note.id);
          if (full != null && context.mounted) {
            Navigator.push(context, MaterialPageRoute(
              builder: (_) => ChangeNotifierProvider.value(
                value: state,
                child: NoteEditorScreen(note: full))));
          }
        },
        child: const Icon(Icons.add),
      ),
    );
  }
}

class _NotesHeader extends StatelessWidget {
  final String title;
  final ValueChanged<String> onSearch;
  final VoidCallback onNewNote;
  const _NotesHeader({required this.title, required this.onSearch, required this.onNewNote});

  @override
  Widget build(BuildContext context) {
    return Padding(padding: const EdgeInsets.all(12), child: Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Row(children: [
          Expanded(child: Text(title, style: Theme.of(context).textTheme.titleMedium?.copyWith(fontWeight: FontWeight.bold))),
          IconButton(icon: const Icon(Icons.add, color: Color(0xFF6366F1)), onPressed: onNewNote, tooltip: 'New note'),
        ]),
        TextField(onChanged: onSearch, decoration: InputDecoration(
          hintText: 'Search notes...',
          prefixIcon: const Icon(Icons.search, size: 18),
          isDense: true,
          border: OutlineInputBorder(borderRadius: BorderRadius.circular(8), borderSide: BorderSide.none),
          filled: true,
        )),
      ],
    ));
  }
}

class _OfflineBanner extends StatelessWidget {
  final String? message;
  const _OfflineBanner({this.message});

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    return Material(
      color: scheme.secondaryContainer,
      child: SafeArea(
        bottom: false,
        child: Padding(
          padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 6),
          child: Row(children: [
            Icon(Icons.cloud_off, size: 16, color: scheme.onSecondaryContainer),
            const SizedBox(width: 8),
            Expanded(child: Text(
              message ?? 'Offline mode — backend unavailable',
              style: TextStyle(fontSize: 12, color: scheme.onSecondaryContainer),
            )),
          ]),
        ),
      ),
    );
  }
}

class _EmptyEditor extends StatelessWidget {
  @override
  Widget build(BuildContext context) {
    return Center(child: Column(mainAxisSize: MainAxisSize.min, children: [
      Icon(Icons.edit_note, size: 64, color: Theme.of(context).colorScheme.onSurface.withOpacity(0.2)),
      const SizedBox(height: 16),
      Text('Select a note or create one',
        style: TextStyle(color: Theme.of(context).colorScheme.onSurface.withOpacity(0.4))),
    ]));
  }
}

Future<String?> _inputDialog(BuildContext context, String title, String hint) {
  final ctrl = TextEditingController();
  return showDialog<String>(
    context: context,
    builder: (ctx) => AlertDialog(
      title: Text(title),
      content: TextField(controller: ctrl, autofocus: true,
        decoration: InputDecoration(hintText: hint),
        onSubmitted: (v) => Navigator.pop(ctx, v)),
      actions: [
        TextButton(onPressed: () => Navigator.pop(ctx), child: const Text('Cancel')),
        FilledButton(onPressed: () => Navigator.pop(ctx, ctrl.text), child: const Text('Create')),
      ],
    ),
  );
}
