// lib/screens/note_editor_screen.dart
// Éditeur de note — texte riche + canvas manuscrit

import 'dart:async';
import 'package:flutter/material.dart';
import 'package:provider/provider.dart';
import '../services/api_client.dart';
import '../services/app_state.dart';
import '../widgets/ink_canvas.dart';

class NoteEditorScreen extends StatefulWidget {
  final Note note;
  const NoteEditorScreen({super.key, required this.note});

  @override
  State<NoteEditorScreen> createState() => _NoteEditorScreenState();
}

class _NoteEditorScreenState extends State<NoteEditorScreen> {
  late TextEditingController _titleCtrl;
  late TextEditingController _contentCtrl;
  Timer? _saveTimer;
  bool _isSaving = false;
  bool _hasChanges = false;
  late Note _note;
  bool _showInkCanvas = false;

  @override
  void initState() {
    super.initState();
    _note = widget.note;
    _showInkCanvas = _note.noteType == 'handwritten' || _note.noteType == 'mixed';
    _titleCtrl = TextEditingController(text: _note.title);
    _contentCtrl = TextEditingController(
      text: _note.pages?.isNotEmpty == true
          ? _note.pages!.first.typedContent
          : '',
    );
    _titleCtrl.addListener(_onChanged);
    _contentCtrl.addListener(_onChanged);
  }

  void _onChanged() {
    if (!_hasChanges) setState(() => _hasChanges = true);
    _saveTimer?.cancel();
    _saveTimer = Timer(const Duration(seconds: 2), _save);
  }

  Future<void> _save() async {
    if (!_hasChanges) return;
    setState(() => _isSaving = true);
    final state = context.read<AppState>();
    try {
      if (_titleCtrl.text != _note.title) {
        await state.updateNoteTitle(_note.id, _titleCtrl.text);
      }
      if (!_showInkCanvas) {
        await state.savePageText(_note.id, 1, _contentCtrl.text);
      }
      setState(() => _hasChanges = false);
    } catch (e) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text('Save failed: $e'), backgroundColor: Colors.red),
        );
      }
    }
    if (mounted) setState(() => _isSaving = false);
  }

  Future<void> _saveInk(List<Map<String, dynamic>> strokes) async {
    final state = context.read<AppState>();
    try {
      await context.read<AppState>().client.savePageInk(_note.id, 1, strokes);
    } catch (e) {
      debugPrint('Ink save error: $e');
    }
  }

  @override
  void dispose() {
    _saveTimer?.cancel();
    _save();
    _titleCtrl.dispose();
    _contentCtrl.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;

    return Column(
      children: [
        // ── Top bar ──────────────────────────────────────────────
        Container(
          height: 48,
          padding: const EdgeInsets.symmetric(horizontal: 16),
          decoration: BoxDecoration(
            border: Border(bottom: BorderSide(color: scheme.outlineVariant)),
          ),
          child: Row(
            children: [
              if (MediaQuery.of(context).size.width <= 800)
                IconButton(
                  icon: const Icon(Icons.arrow_back),
                  onPressed: () => Navigator.pop(context),
                  visualDensity: VisualDensity.compact,
                ),

              // Toggle texte / manuscrit
              Container(
                decoration: BoxDecoration(
                  color: scheme.surfaceContainerHighest.withOpacity(0.5),
                  borderRadius: BorderRadius.circular(8),
                ),
                child: Row(
                  mainAxisSize: MainAxisSize.min,
                  children: [
                    _ModeBtn(
                      icon: Icons.text_snippet_outlined,
                      label: 'Text',
                      selected: !_showInkCanvas,
                      onTap: () => setState(() => _showInkCanvas = false),
                    ),
                    _ModeBtn(
                      icon: Icons.draw_outlined,
                      label: 'Draw',
                      selected: _showInkCanvas,
                      onTap: () => setState(() => _showInkCanvas = true),
                    ),
                  ],
                ),
              ),

              const Spacer(),

              // Statut sauvegarde
              if (_isSaving)
                const Row(mainAxisSize: MainAxisSize.min, children: [
                  SizedBox(width: 12, height: 12,
                    child: CircularProgressIndicator(strokeWidth: 1.5, color: Color(0xFF6366F1))),
                  SizedBox(width: 6),
                  Text('Saving...', style: TextStyle(fontSize: 12, color: Colors.grey)),
                ])
              else if (_hasChanges)
                const Text('Unsaved', style: TextStyle(fontSize: 12, color: Colors.orange))
              else
                const Row(mainAxisSize: MainAxisSize.min, children: [
                  Icon(Icons.check_circle_outline, size: 14, color: Colors.green),
                  SizedBox(width: 4),
                  Text('Saved', style: TextStyle(fontSize: 12, color: Colors.green)),
                ]),

              IconButton(
                icon: const Icon(Icons.more_vert, size: 18),
                onPressed: () => _showNoteMenu(context),
                visualDensity: VisualDensity.compact,
              ),
            ],
          ),
        ),

        // ── Titre ─────────────────────────────────────────────────
        Padding(
          padding: const EdgeInsets.fromLTRB(24, 16, 24, 4),
          child: TextField(
            controller: _titleCtrl,
            style: Theme.of(context).textTheme.headlineSmall?.copyWith(
                  fontWeight: FontWeight.bold,
                ),
            decoration: const InputDecoration(
              hintText: 'Untitled',
              border: InputBorder.none,
              contentPadding: EdgeInsets.zero,
            ),
          ),
        ),

        const Divider(height: 1),

        // ── Contenu — texte ou canvas ─────────────────────────────
        Expanded(
          child: _showInkCanvas
              ? InkCanvas(
                  initialStrokes: _note.pages?.isNotEmpty == true
                      ? _note.pages!.first.strokes
                          .map((s) => s is Map<String, dynamic>
                              ? s
                              : <String, dynamic>{})
                          .toList()
                      : [],
                  template: _note.pages?.isNotEmpty == true
                      ? _note.pages!.first.template
                      : 'blank',
                  onStrokesChanged: _saveInk,
                )
              : Column(
                  children: [
                    _FormatToolbar(controller: _contentCtrl),
                    const Divider(height: 1),
                    Expanded(
                      child: Padding(
                        padding: const EdgeInsets.symmetric(horizontal: 24, vertical: 12),
                        child: TextField(
                          controller: _contentCtrl,
                          maxLines: null,
                          expands: true,
                          textAlignVertical: TextAlignVertical.top,
                          style: const TextStyle(fontSize: 15, height: 1.7),
                          decoration: const InputDecoration(
                            hintText: 'Start writing...',
                            border: InputBorder.none,
                            contentPadding: EdgeInsets.zero,
                          ),
                        ),
                      ),
                    ),
                    // Status bar
                    Container(
                      height: 28,
                      padding: const EdgeInsets.symmetric(horizontal: 16),
                      decoration: BoxDecoration(
                        border: Border(top: BorderSide(color: scheme.outlineVariant)),
                      ),
                      child: Row(children: [
                        Text(
                          '${_contentCtrl.text.split(' ').where((w) => w.isNotEmpty).length} words',
                          style: TextStyle(fontSize: 11, color: scheme.onSurface.withOpacity(0.4)),
                        ),
                        const SizedBox(width: 16),
                        Text(
                          '${_contentCtrl.text.length} chars',
                          style: TextStyle(fontSize: 11, color: scheme.onSurface.withOpacity(0.4)),
                        ),
                      ]),
                    ),
                  ],
                ),
        ),
      ],
    );
  }

  void _showNoteMenu(BuildContext context) {
    showModalBottomSheet(
      context: context,
      builder: (_) => Column(
        mainAxisSize: MainAxisSize.min,
        children: [
          ListTile(
            leading: const Icon(Icons.delete_outline, color: Colors.red),
            title: const Text('Delete', style: TextStyle(color: Colors.red)),
            onTap: () {
              Navigator.pop(context);
              context.read<AppState>().deleteNote(_note.id);
              if (MediaQuery.of(context).size.width <= 800) {
                Navigator.pop(context);
              }
            },
          ),
        ],
      ),
    );
  }
}

// ── Mode toggle button ───────────────────────────────────────────

class _ModeBtn extends StatelessWidget {
  final IconData icon;
  final String label;
  final bool selected;
  final VoidCallback onTap;

  const _ModeBtn({
    required this.icon,
    required this.label,
    required this.selected,
    required this.onTap,
  });

  @override
  Widget build(BuildContext context) {
    return GestureDetector(
      onTap: onTap,
      child: Container(
        padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 6),
        decoration: BoxDecoration(
          color: selected ? const Color(0xFF6366F1) : Colors.transparent,
          borderRadius: BorderRadius.circular(8),
        ),
        child: Row(
          mainAxisSize: MainAxisSize.min,
          children: [
            Icon(icon, size: 16,
                color: selected ? Colors.white : Theme.of(context).colorScheme.onSurface.withOpacity(0.6)),
            const SizedBox(width: 4),
            Text(label,
                style: TextStyle(
                  fontSize: 12,
                  fontWeight: selected ? FontWeight.w600 : FontWeight.normal,
                  color: selected ? Colors.white : Theme.of(context).colorScheme.onSurface.withOpacity(0.6),
                )),
          ],
        ),
      ),
    );
  }
}

// ── Format toolbar ───────────────────────────────────────────────

class _FormatToolbar extends StatelessWidget {
  final TextEditingController controller;
  const _FormatToolbar({required this.controller});

  void _wrap(String before, String after) {
    final sel = controller.selection;
    if (!sel.isValid) return;
    final text = controller.text;
    final selected = sel.textInside(text);
    final newText = text.replaceRange(sel.start, sel.end, '$before$selected$after');
    controller.value = controller.value.copyWith(
      text: newText,
      selection: TextSelection(
        baseOffset: sel.start + before.length,
        extentOffset: sel.start + before.length + selected.length,
      ),
    );
  }

  void _insertLine(String prefix) {
    final sel = controller.selection;
    final text = controller.text;
    final lineStart = text.lastIndexOf('\n', sel.start - 1) + 1;
    final newText = text.replaceRange(lineStart, lineStart, '$prefix ');
    controller.value = controller.value.copyWith(
      text: newText,
      selection: TextSelection.collapsed(offset: sel.baseOffset + prefix.length + 1),
    );
  }

  @override
  Widget build(BuildContext context) {
    return SingleChildScrollView(
      scrollDirection: Axis.horizontal,
      padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 4),
      child: Row(children: [
        _Btn(icon: Icons.format_bold, tooltip: 'Bold', onTap: () => _wrap('**', '**')),
        _Btn(icon: Icons.format_italic, tooltip: 'Italic', onTap: () => _wrap('*', '*')),
        _Btn(icon: Icons.code, tooltip: 'Code', onTap: () => _wrap('`', '`')),
        const VerticalDivider(width: 16, indent: 6, endIndent: 6),
        _Btn(icon: Icons.title, tooltip: 'Heading', onTap: () => _insertLine('#')),
        _Btn(icon: Icons.format_list_bulleted, tooltip: 'Bullet', onTap: () => _insertLine('-')),
        _Btn(icon: Icons.check_box_outlined, tooltip: 'Checkbox', onTap: () => _insertLine('- [ ]')),
        _Btn(icon: Icons.format_quote, tooltip: 'Quote', onTap: () => _insertLine('>')),
      ]),
    );
  }
}

class _Btn extends StatelessWidget {
  final IconData icon;
  final String tooltip;
  final VoidCallback onTap;
  const _Btn({required this.icon, required this.tooltip, required this.onTap});

  @override
  Widget build(BuildContext context) {
    return Tooltip(
      message: tooltip,
      child: InkWell(
        onTap: onTap,
        borderRadius: BorderRadius.circular(6),
        child: Padding(
          padding: const EdgeInsets.all(6),
          child: Icon(icon, size: 18,
              color: Theme.of(context).colorScheme.onSurface.withOpacity(0.6)),
        ),
      ),
    );
  }
}

