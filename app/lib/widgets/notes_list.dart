// lib/widgets/notes_list.dart

import 'package:flutter/material.dart';
import '../services/api_client.dart';

class NotesList extends StatelessWidget {
  final List<Note> notes;
  final Note? selected;
  final bool isLoading;
  final ValueChanged<Note> onSelect;
  final ValueChanged<Note> onDelete;

  const NotesList({
    super.key,
    required this.notes,
    required this.selected,
    required this.isLoading,
    required this.onSelect,
    required this.onDelete,
  });

  @override
  Widget build(BuildContext context) {
    if (isLoading) {
      return const Center(
          child: CircularProgressIndicator(color: Color(0xFF6366F1)));
    }

    if (notes.isEmpty) {
      return Center(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            Icon(Icons.note_add_outlined,
                size: 48,
                color: Theme.of(context)
                    .colorScheme
                    .onSurface
                    .withOpacity(0.2)),
            const SizedBox(height: 12),
            Text('No notes yet',
                style: TextStyle(
                    color: Theme.of(context)
                        .colorScheme
                        .onSurface
                        .withOpacity(0.4))),
          ],
        ),
      );
    }

    // Pinned notes first
    final pinned = notes.where((n) => n.isPinned).toList();
    final regular = notes.where((n) => !n.isPinned).toList();

    return ListView(
      children: [
        if (pinned.isNotEmpty) ...[
          _SectionHeader(title: 'PINNED'),
          ...pinned.map((n) => _NoteCard(
                note: n,
                isSelected: selected?.id == n.id,
                onTap: () => onSelect(n),
                onDelete: () => onDelete(n),
              )),
          _SectionHeader(title: 'NOTES'),
        ],
        ...regular.map((n) => _NoteCard(
              note: n,
              isSelected: selected?.id == n.id,
              onTap: () => onSelect(n),
              onDelete: () => onDelete(n),
            )),
      ],
    );
  }
}

class _SectionHeader extends StatelessWidget {
  final String title;
  const _SectionHeader({required this.title});

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.fromLTRB(16, 12, 16, 4),
      child: Text(
        title,
        style: TextStyle(
          fontSize: 11,
          fontWeight: FontWeight.w600,
          color: Theme.of(context).colorScheme.onSurface.withOpacity(0.4),
          letterSpacing: 0.8,
        ),
      ),
    );
  }
}

class _NoteCard extends StatelessWidget {
  final Note note;
  final bool isSelected;
  final VoidCallback onTap;
  final VoidCallback onDelete;

  const _NoteCard({
    required this.note,
    required this.isSelected,
    required this.onTap,
    required this.onDelete,
  });

  String _formatDate(String isoDate) {
    try {
      final dt = DateTime.parse(isoDate).toLocal();
      final now = DateTime.now();
      final diff = now.difference(dt);
      if (diff.inDays == 0) return 'Today';
      if (diff.inDays == 1) return 'Yesterday';
      if (diff.inDays < 7) return '${diff.inDays} days ago';
      return '${dt.day}/${dt.month}/${dt.year}';
    } catch (_) {
      return '';
    }
  }

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    final isHandwritten = note.noteType == 'handwritten';

    return Dismissible(
      key: Key(note.id),
      direction: DismissDirection.endToStart,
      background: Container(
        alignment: Alignment.centerRight,
        padding: const EdgeInsets.only(right: 16),
        color: scheme.error,
        child: const Icon(Icons.delete_outline, color: Colors.white),
      ),
      confirmDismiss: (_) async {
        return await showDialog<bool>(
          context: context,
          builder: (ctx) => AlertDialog(
            title: const Text('Delete note?'),
            content: Text('Move "${note.title}" to trash?'),
            actions: [
              TextButton(
                  onPressed: () => Navigator.pop(ctx, false),
                  child: const Text('Cancel')),
              FilledButton(
                  onPressed: () => Navigator.pop(ctx, true),
                  style: FilledButton.styleFrom(
                      backgroundColor: scheme.error),
                  child: const Text('Delete')),
            ],
          ),
        );
      },
      onDismissed: (_) => onDelete(),
      child: InkWell(
        onTap: onTap,
        child: Container(
          margin: const EdgeInsets.symmetric(horizontal: 8, vertical: 2),
          decoration: BoxDecoration(
            color: isSelected
                ? const Color(0xFF6366F1).withOpacity(0.1)
                : Colors.transparent,
            borderRadius: BorderRadius.circular(10),
            border: isSelected
                ? Border.all(
                    color: const Color(0xFF6366F1).withOpacity(0.3))
                : null,
          ),
          child: Padding(
            padding:
                const EdgeInsets.symmetric(horizontal: 12, vertical: 10),
            child: Row(
              children: [
                // Type icon
                Container(
                  width: 32,
                  height: 32,
                  decoration: BoxDecoration(
                    color: isHandwritten
                        ? const Color(0xFFEC4899).withOpacity(0.12)
                        : const Color(0xFF6366F1).withOpacity(0.12),
                    borderRadius: BorderRadius.circular(8),
                  ),
                  child: Icon(
                    isHandwritten ? Icons.draw_outlined : Icons.text_snippet_outlined,
                    size: 16,
                    color: isHandwritten
                        ? const Color(0xFFEC4899)
                        : const Color(0xFF6366F1),
                  ),
                ),
                const SizedBox(width: 10),
                Expanded(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Row(
                        children: [
                          Expanded(
                            child: Text(
                              note.title,
                              style: TextStyle(
                                fontSize: 14,
                                fontWeight: FontWeight.w500,
                                color: isSelected
                                    ? const Color(0xFF6366F1)
                                    : null,
                              ),
                              overflow: TextOverflow.ellipsis,
                            ),
                          ),
                          if (note.isPinned)
                            const Icon(Icons.push_pin,
                                size: 12,
                                color: Color(0xFF6366F1)),
                        ],
                      ),
                      const SizedBox(height: 2),
                      Row(
                        children: [
                          Text(
                            _formatDate(note.updatedAt),
                            style: TextStyle(
                              fontSize: 11,
                              color: scheme.onSurface.withOpacity(0.4),
                            ),
                          ),
                          if (note.tags.isNotEmpty) ...[
                            const SizedBox(width: 6),
                            ...note.tags.take(2).map((tag) => Container(
                                  margin: const EdgeInsets.only(right: 4),
                                  padding: const EdgeInsets.symmetric(
                                      horizontal: 6, vertical: 1),
                                  decoration: BoxDecoration(
                                    color: scheme.surfaceContainerHighest,
                                    borderRadius: BorderRadius.circular(4),
                                  ),
                                  child: Text(tag,
                                      style: TextStyle(
                                          fontSize: 10,
                                          color: scheme.onSurface
                                              .withOpacity(0.5))),
                                )),
                          ],
                        ],
                      ),
                    ],
                  ),
                ),
              ],
            ),
          ),
        ),
      ),
    );
  }
}
