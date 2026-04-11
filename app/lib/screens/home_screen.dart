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
      body: Row(children: [
        SizedBox(width: 240, child: NotebookSidebar(
          notebooks: state.notebooks,
          selected: state.selectedNotebook,
          onSelect: state.selectNotebook,
          onCreate: () => _createNotebook(context),
          onSync: () => _sync(context),
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
            onSelect: (note) async {
              final full = await state.client.getNote(note.id);
              state.selectNote(full);
            },
            onDelete: (note) => state.deleteNote(note.id),
          )),
        ])),
        VerticalDivider(width: 1, color: scheme.outlineVariant),
        Expanded(child: state.selectedNote != null
          ? NoteEditorScreen(note: state.selectedNote!)
          : _EmptyEditor()),
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
    final full = await state.client.getNote(note.id);
    state.selectNote(full);
  }

  Future<void> _sync(BuildContext context) async {
    final msg = await context.read<AppState>().triggerSync();
    if (context.mounted) {
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text(msg), duration: const Duration(seconds: 3)));
    }
  }
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
          IconButton(icon: const Icon(Icons.sync), onPressed: () async {
            final msg = await state.triggerSync();
            if (context.mounted) ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(msg)));
          }),
        ],
      ),
      drawer: Drawer(child: NotebookSidebar(
        notebooks: state.notebooks,
        selected: state.selectedNotebook,
        onSelect: (nb) { state.selectNotebook(nb); Navigator.pop(context); },
        onCreate: () async {
          final name = await _inputDialog(context, 'New Notebook', 'Name');
          if (name != null) await state.createNotebook(name, '#6366f1');
        },
        onSync: () async {
          final msg = await state.triggerSync();
          if (context.mounted) ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(msg)));
        },
      )),
      body: NotesList(
        notes: state.notes,
        selected: state.selectedNote,
        isLoading: state.isLoading,
        onSelect: (note) async {
          final full = await state.client.getNote(note.id);
          state.selectNote(full);
          if (context.mounted) {
            Navigator.push(context, MaterialPageRoute(
              builder: (_) => ChangeNotifierProvider.value(
                value: state,
                child: NoteEditorScreen(note: full))));
          }
        },
        onDelete: (note) => state.deleteNote(note.id),
      ),
      floatingActionButton: FloatingActionButton(
        backgroundColor: const Color(0xFF6366F1),
        foregroundColor: Colors.white,
        onPressed: () async {
          final note = await state.createNote(title: 'Untitled', noteType: 'typed');
          final full = await state.client.getNote(note.id);
          state.selectNote(full);
          if (context.mounted) {
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
