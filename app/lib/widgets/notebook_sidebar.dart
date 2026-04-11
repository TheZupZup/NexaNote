// lib/widgets/notebook_sidebar.dart

import 'package:flutter/material.dart';
import '../services/api_client.dart';
import '../screens/settings_screen.dart';

class NotebookSidebar extends StatelessWidget {
  final List<Notebook> notebooks;
  final Notebook? selected;
  final ValueChanged<Notebook?> onSelect;
  final VoidCallback onCreate;
  final VoidCallback onSync;

  const NotebookSidebar({
    super.key,
    required this.notebooks,
    required this.selected,
    required this.onSelect,
    required this.onCreate,
    required this.onSync,
  });

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;

    return Column(
      children: [
        // Header
        Padding(
          padding: const EdgeInsets.fromLTRB(16, 16, 8, 8),
          child: Row(
            children: [
              Container(
                width: 28,
                height: 28,
                decoration: BoxDecoration(
                  color: const Color(0xFF6366F1),
                  borderRadius: BorderRadius.circular(7),
                ),
                child: const Icon(Icons.edit_note,
                    color: Colors.white, size: 18),
              ),
              const SizedBox(width: 10),
              Text('NexaNote',
                  style: Theme.of(context).textTheme.titleMedium?.copyWith(
                        fontWeight: FontWeight.bold,
                        color: const Color(0xFF6366F1),
                      )),
              const Spacer(),
              IconButton(
                icon: const Icon(Icons.sync, size: 18),
                onPressed: onSync,
                tooltip: 'Sync',
                visualDensity: VisualDensity.compact,
              ),
            ],
          ),
        ),

        const Divider(height: 1),

        // All Notes
        ListTile(
          dense: true,
          leading: Icon(
            Icons.notes,
            size: 20,
            color: selected == null
                ? const Color(0xFF6366F1)
                : scheme.onSurface.withOpacity(0.6),
          ),
          title: const Text('All Notes', style: TextStyle(fontSize: 14)),
          selected: selected == null,
          selectedColor: const Color(0xFF6366F1),
          selectedTileColor: const Color(0xFF6366F1).withOpacity(0.08),
          shape:
              RoundedRectangleBorder(borderRadius: BorderRadius.circular(8)),
          onTap: () => onSelect(null),
        ),

        // Section header
        Padding(
          padding: const EdgeInsets.fromLTRB(16, 12, 8, 4),
          child: Row(
            children: [
              Text('NOTEBOOKS',
                  style: TextStyle(
                      fontSize: 11,
                      fontWeight: FontWeight.w600,
                      color: scheme.onSurface.withOpacity(0.4),
                      letterSpacing: 0.8)),
              const Spacer(),
              IconButton(
                icon: const Icon(Icons.add, size: 16),
                onPressed: onCreate,
                tooltip: 'New notebook',
                visualDensity: VisualDensity.compact,
              ),
            ],
          ),
        ),

        // Notebook list
        Expanded(
          child: ListView.builder(
            padding: const EdgeInsets.symmetric(horizontal: 8),
            itemCount: notebooks.length,
            itemBuilder: (context, i) {
              final nb = notebooks[i];
              final isSelected = selected?.id == nb.id;
              Color nbColor;
              try {
                nbColor = Color(
                    int.parse(nb.color.replaceAll('#', '0xFF')));
              } catch (_) {
                nbColor = const Color(0xFF6366F1);
              }

              return ListTile(
                dense: true,
                leading: Container(
                  width: 20,
                  height: 20,
                  decoration: BoxDecoration(
                    color: nbColor,
                    borderRadius: BorderRadius.circular(5),
                  ),
                  child: const Icon(Icons.book,
                      color: Colors.white, size: 12),
                ),
                title: Text(nb.name,
                    style: const TextStyle(fontSize: 14),
                    overflow: TextOverflow.ellipsis),
                selected: isSelected,
                selectedColor: const Color(0xFF6366F1),
                selectedTileColor:
                    const Color(0xFF6366F1).withOpacity(0.08),
                shape: RoundedRectangleBorder(
                    borderRadius: BorderRadius.circular(8)),
                onTap: () => onSelect(nb),
              );
            },
          ),
        ),

        const Divider(height: 1),

        // Bottom — settings
        ListTile(
          dense: true,
          leading: Icon(Icons.settings_outlined,
              size: 18, color: scheme.onSurface.withOpacity(0.5)),
          title: Text('Settings',
              style: TextStyle(
                  fontSize: 13,
                  color: scheme.onSurface.withOpacity(0.6))),
          onTap: () => Navigator.push(
            context,
            MaterialPageRoute(builder: (_) => const SettingsScreen()),
          ),
        ),
      ],
    );
  }
}
