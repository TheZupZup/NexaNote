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
  bool _inkSaveFailed = false;
  late Note _note;
  bool _showInk = false;

  /// The current ink strokes (editor/wire shape). Held in memory so toggling
  /// Text↔Draw rebuilds the canvas from the *latest* drawing rather than the
  /// strokes the note was first opened with — switching modes never loses an
  /// unsaved-to-screen stroke.
  late List<Map<String, dynamic>> _inkStrokes;

  /// Captured once dependencies are available so [dispose] can flush pending
  /// edits without touching `context` (which is unsafe after unmount) or
  /// `setState` (which throws on an unmounted widget). Nullable because some
  /// pure layout tests pump the editor without an AppState provider; those
  /// never make edits, so the flush is a no-op there.
  AppState? _appState;

  @override
  void initState() {
    super.initState();
    _note = widget.note;
    _showInk = _note.noteType == 'handwritten' || _note.noteType == 'mixed';
    _titleCtrl = TextEditingController(text: _note.title);
    _contentCtrl = TextEditingController(
      text: _note.pages?.isNotEmpty == true ? _note.pages!.first.typedContent : '');
    _inkStrokes = _note.pages?.isNotEmpty == true
        ? _note.pages!.first.strokes.whereType<Map<String, dynamic>>().toList()
        : <Map<String, dynamic>>[];
    _titleCtrl.addListener(_onChanged);
    _contentCtrl.addListener(_onChanged);
  }

  @override
  void didChangeDependencies() {
    super.didChangeDependencies();
    // Read defensively: layout tests pump this screen without a provider.
    try {
      _appState = context.read<AppState>();
    } catch (_) {
      _appState = null;
    }
  }

  void _onChanged() {
    if (!_hasChanges) setState(() => _hasChanges = true);
    _saveTimer?.cancel();
    _saveTimer = Timer(const Duration(seconds: 2), _save);
  }

  Future<void> _save() async {
    if (!_hasChanges) return;
    if (mounted) setState(() => _isSaving = true);
    final ok = await _persist();
    if (!mounted) return;
    setState(() {
      _isSaving = false;
      if (ok) _hasChanges = false;
    });
  }

  /// Writes the title and (in text mode) the body to local/remote storage.
  /// Returns whether the write succeeded. Deliberately free of `setState`/
  /// `context` use so [dispose] can call it to flush the last edits even after
  /// the widget has been unmounted — the bug that previously lost anything
  /// typed in the final debounce window.
  Future<bool> _persist() async {
    if (!_hasChanges) return true;
    final state = _appState;
    if (state == null) return false;
    try {
      if (_titleCtrl.text != _note.title) {
        await state.updateNoteTitle(_note.id, _titleCtrl.text);
      }
      if (!_showInk) await state.savePageText(_note.id, 1, _contentCtrl.text);
      return true;
    } catch (e) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text('Save failed: $e'), backgroundColor: Colors.red));
      }
      return false;
    }
  }

  Future<void> _saveInk(List<Map<String, dynamic>> strokes) async {
    // Keep the on-screen drawing in memory first so a failed persist (or a
    // mode toggle) never drops what the user just drew.
    _inkStrokes = strokes;
    final state = _appState;
    if (state == null) return;
    try {
      await state.savePageInk(_note.id, 1, strokes);
      _inkSaveFailed = false;
    } catch (e) {
      debugPrint('Ink save error: $e');
      // Surface a friendly, throttled error rather than silently dropping the
      // stroke. The drawing stays on screen regardless.
      if (mounted && !_inkSaveFailed) {
        _inkSaveFailed = true;
        ScaffoldMessenger.of(context).showSnackBar(const SnackBar(
          content: Text('Could not save drawing — it stays on screen, retrying as you draw.'),
          backgroundColor: Colors.red));
      }
    }
  }

  @override
  void dispose() {
    _saveTimer?.cancel();
    // Flush any pending text edits synchronously-scheduled before teardown.
    // _persist captures its AppState reference, so this is safe post-unmount.
    _persist();
    _titleCtrl.dispose();
    _contentCtrl.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    // The editor renders inside two very different containers:
    //  * On wide layouts it is embedded as a side panel inside HomeScreen's
    //    Scaffold, which already provides a Material ancestor, a background and
    //    has no system intrusions to dodge.
    //  * On phones it is pushed as its own full-screen route, so it has to
    //    bring its own Scaffold (Material + themed canvas + soft-keyboard inset
    //    handling) and a SafeArea so the header clears the status bar/notch and
    //    the footer clears the gesture navigation bar.
    // 800px matches the breakpoint HomeScreen uses to pick desktop vs mobile.
    final isMobile = MediaQuery.of(context).size.width <= 800;
    final editor = _buildEditor(isMobile);
    if (!isMobile) return editor;
    return Scaffold(
      backgroundColor: Theme.of(context).colorScheme.surface,
      body: SafeArea(child: editor),
    );
  }

  Widget _buildEditor(bool isMobile) {
    return LayoutBuilder(builder: (context, constraints) {
      // Drive spacing and header density from the *actual* width the editor is
      // given — which on wide layouts is the panel, not the whole window — so a
      // narrow panel reflows just like a phone would.
      final width = constraints.maxWidth;
      final compactHeader = width < 420;
      final hPad = width < 380 ? 16.0 : 24.0;

      return Column(children: [
        _EditorHeader(
          showBack: isMobile,
          compact: compactHeader,
          showInk: _showInk,
          isSaving: _isSaving,
          hasChanges: _hasChanges,
          onBack: () => Navigator.pop(context),
          onText: () => setState(() => _showInk = false),
          onDraw: () => setState(() => _showInk = true),
          onDelete: () {
            context.read<AppState>().deleteNote(_note.id);
            if (isMobile) Navigator.pop(context);
          },
        ),
        // Title
        Padding(
          padding: EdgeInsets.fromLTRB(hPad, 16, hPad, 8),
          child: TextField(
            controller: _titleCtrl,
            textCapitalization: TextCapitalization.sentences,
            style: Theme.of(context).textTheme.headlineSmall?.copyWith(fontWeight: FontWeight.bold),
            decoration: const InputDecoration(
              hintText: 'Untitled', border: InputBorder.none, contentPadding: EdgeInsets.zero)),
        ),
        const Divider(height: 1),
        // Content
        Expanded(child: _showInk
          ? InkCanvas(
              initialStrokes: _inkStrokes,
              template: _note.pages?.isNotEmpty == true ? _note.pages!.first.template : 'blank',
              onStrokesChanged: _saveInk)
          : _TextBody(controller: _contentCtrl, horizontalPadding: hPad)),
      ]);
    });
  }
}

/// The top chrome of the editor: back button (phones only), the Text/Draw mode
/// toggle, a save-status indicator and the delete action. Reflows to an
/// icon-only [compact] form on narrow widths so the controls never collide.
class _EditorHeader extends StatelessWidget {
  final bool showBack;
  final bool compact;
  final bool showInk;
  final bool isSaving;
  final bool hasChanges;
  final VoidCallback onBack;
  final VoidCallback onText;
  final VoidCallback onDraw;
  final VoidCallback onDelete;
  const _EditorHeader({
    required this.showBack,
    required this.compact,
    required this.showInk,
    required this.isSaving,
    required this.hasChanges,
    required this.onBack,
    required this.onText,
    required this.onDraw,
    required this.onDelete,
  });

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    return Container(
      // A min-height (rather than a fixed height) keeps comfortable touch
      // targets while letting the bar grow if text scaling needs it.
      constraints: const BoxConstraints(minHeight: 56),
      padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 6),
      decoration: BoxDecoration(
        border: Border(bottom: BorderSide(color: scheme.outlineVariant))),
      child: Row(children: [
        if (showBack)
          IconButton(
            icon: const Icon(Icons.arrow_back),
            tooltip: 'Back',
            visualDensity: VisualDensity.compact,
            onPressed: onBack),
        _ModeToggle(showInk: showInk, compact: compact, onText: onText, onDraw: onDraw),
        const SizedBox(width: 8),
        // Absorbs the remaining width and right-aligns the status so the toggle
        // and the delete action never crowd each other.
        Expanded(child: Align(
          alignment: Alignment.centerRight,
          child: _SaveStatus(isSaving: isSaving, hasChanges: hasChanges, compact: compact))),
        const SizedBox(width: 4),
        IconButton(
          icon: const Icon(Icons.delete_outline),
          iconSize: 20,
          tooltip: 'Delete note',
          visualDensity: VisualDensity.compact,
          onPressed: onDelete),
      ]),
    );
  }
}

/// Segmented Text/Draw switch. Drops the text labels (icon-only) when [compact]
/// so it stays narrow on small phones.
class _ModeToggle extends StatelessWidget {
  final bool showInk;
  final bool compact;
  final VoidCallback onText;
  final VoidCallback onDraw;
  const _ModeToggle({required this.showInk, required this.compact, required this.onText, required this.onDraw});

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    return Container(
      padding: const EdgeInsets.all(3),
      decoration: BoxDecoration(
        color: scheme.surfaceContainerHighest.withOpacity(0.5),
        borderRadius: BorderRadius.circular(10)),
      child: Row(mainAxisSize: MainAxisSize.min, children: [
        _ModeBtn(icon: Icons.text_snippet_outlined, label: 'Text', selected: !showInk, compact: compact, onTap: onText),
        _ModeBtn(icon: Icons.draw_outlined, label: 'Draw', selected: showInk, compact: compact, onTap: onDraw),
      ]),
    );
  }
}

class _ModeBtn extends StatelessWidget {
  final IconData icon;
  final String label;
  final bool selected;
  final bool compact;
  final VoidCallback onTap;
  const _ModeBtn({required this.icon, required this.label, required this.selected, required this.compact, required this.onTap});

  @override
  Widget build(BuildContext context) {
    final fg = selected ? Colors.white : Theme.of(context).colorScheme.onSurface.withOpacity(0.6);
    return Tooltip(
      message: label,
      child: GestureDetector(
        onTap: onTap,
        behavior: HitTestBehavior.opaque,
        child: AnimatedContainer(
          duration: const Duration(milliseconds: 150),
          padding: EdgeInsets.symmetric(horizontal: compact ? 12 : 14, vertical: 10),
          decoration: BoxDecoration(
            color: selected ? const Color(0xFF6366F1) : Colors.transparent,
            borderRadius: BorderRadius.circular(8)),
          child: Row(mainAxisSize: MainAxisSize.min, children: [
            Icon(icon, size: 18, color: fg),
            if (!compact) ...[
              const SizedBox(width: 6),
              Text(label, style: TextStyle(
                fontSize: 13,
                fontWeight: selected ? FontWeight.w600 : FontWeight.normal,
                color: fg)),
            ],
          ]),
        ),
      ),
    );
  }
}

/// Shows whether the note is saving / has unsaved edits / is saved. Collapses to
/// just its icon (with a tooltip) when [compact] so it never pushes the header
/// into an overflow on small screens.
class _SaveStatus extends StatelessWidget {
  final bool isSaving;
  final bool hasChanges;
  final bool compact;
  const _SaveStatus({required this.isSaving, required this.hasChanges, required this.compact});

  @override
  Widget build(BuildContext context) {
    if (isSaving) {
      return _row(
        const SizedBox(width: 14, height: 14,
          child: CircularProgressIndicator(strokeWidth: 2, color: Color(0xFF6366F1))),
        'Saving…', const Color(0xFF6366F1));
    }
    if (hasChanges) {
      return _row(const Icon(Icons.cloud_upload_outlined, size: 16, color: Colors.orange),
        'Unsaved', Colors.orange);
    }
    return _row(const Icon(Icons.check_circle_outline, size: 16, color: Colors.green),
      'Saved', Colors.green);
  }

  Widget _row(Widget leading, String label, Color color) {
    if (compact) return Tooltip(message: label, child: leading);
    return Row(mainAxisSize: MainAxisSize.min, children: [
      leading,
      const SizedBox(width: 6),
      Text(label, style: TextStyle(fontSize: 12, color: color)),
    ]);
  }
}

/// The typed-note body: markdown toolbar, the writing area, and a live
/// word/character footer. Padding is supplied by the parent so the title, body
/// and footer share one responsive gutter.
class _TextBody extends StatelessWidget {
  final TextEditingController controller;
  final double horizontalPadding;
  const _TextBody({required this.controller, required this.horizontalPadding});

  @override
  Widget build(BuildContext context) {
    return Column(children: [
      _FormatToolbar(controller: controller),
      const Divider(height: 1),
      Expanded(child: Padding(
        padding: EdgeInsets.symmetric(horizontal: horizontalPadding, vertical: 12),
        child: TextField(
          controller: controller,
          maxLines: null,
          expands: true,
          textAlignVertical: TextAlignVertical.top,
          keyboardType: TextInputType.multiline,
          textCapitalization: TextCapitalization.sentences,
          style: const TextStyle(fontSize: 15, height: 1.7),
          decoration: const InputDecoration(
            hintText: 'Start writing...', border: InputBorder.none, contentPadding: EdgeInsets.zero)))),
      _WordCountFooter(controller: controller, horizontalPadding: horizontalPadding),
    ]);
  }
}

class _FormatToolbar extends StatelessWidget {
  final TextEditingController controller;
  const _FormatToolbar({required this.controller});

  void _wrap(String b, String a) {
    final sel = controller.selection;
    if (!sel.isValid) return;
    final text = controller.text;
    final selected = sel.textInside(text);
    controller.value = controller.value.copyWith(
      text: text.replaceRange(sel.start, sel.end, '$b$selected$a'),
      selection: TextSelection(baseOffset: sel.start + b.length, extentOffset: sel.start + b.length + selected.length));
  }

  void _line(String prefix) {
    final sel = controller.selection;
    final text = controller.text;
    final start = text.lastIndexOf('\n', sel.start - 1) + 1;
    controller.value = controller.value.copyWith(
      text: text.replaceRange(start, start, '$prefix '),
      selection: TextSelection.collapsed(offset: sel.baseOffset + prefix.length + 1));
  }

  @override
  Widget build(BuildContext context) {
    // Horizontal scrolling guarantees every button stays reachable even when
    // the toolbar is wider than a small phone.
    return SingleChildScrollView(
      scrollDirection: Axis.horizontal,
      padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 4),
      child: Row(children: [
        _Btn(icon: Icons.format_bold, tooltip: 'Bold', onTap: () => _wrap('**', '**')),
        _Btn(icon: Icons.format_italic, tooltip: 'Italic', onTap: () => _wrap('*', '*')),
        _Btn(icon: Icons.code, tooltip: 'Code', onTap: () => _wrap('`', '`')),
        const _ToolbarDivider(),
        _Btn(icon: Icons.title, tooltip: 'Heading', onTap: () => _line('#')),
        _Btn(icon: Icons.format_list_bulleted, tooltip: 'Bullet', onTap: () => _line('-')),
        _Btn(icon: Icons.check_box_outlined, tooltip: 'Checkbox', onTap: () => _line('- [ ]')),
        _Btn(icon: Icons.format_quote, tooltip: 'Quote', onTap: () => _line('>')),
      ]),
    );
  }
}

class _ToolbarDivider extends StatelessWidget {
  const _ToolbarDivider();
  @override
  Widget build(BuildContext context) {
    return Container(
      width: 1,
      height: 24,
      margin: const EdgeInsets.symmetric(horizontal: 6),
      color: Theme.of(context).colorScheme.outlineVariant);
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
      // 12px padding around a 20px icon yields a ~44px touch target, in line
      // with the Material minimum.
      child: InkWell(
        onTap: onTap,
        borderRadius: BorderRadius.circular(10),
        child: Padding(
          padding: const EdgeInsets.all(12),
          child: Icon(icon, size: 20, color: Theme.of(context).colorScheme.onSurface.withOpacity(0.7)))));
  }
}

/// Live word/character count. Listens to the controller so the numbers update
/// as you type rather than only on the next rebuild.
class _WordCountFooter extends StatelessWidget {
  final TextEditingController controller;
  final double horizontalPadding;
  const _WordCountFooter({required this.controller, required this.horizontalPadding});

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    final style = TextStyle(fontSize: 11, color: scheme.onSurface.withOpacity(0.45));
    return Container(
      constraints: const BoxConstraints(minHeight: 36),
      alignment: Alignment.centerLeft,
      padding: EdgeInsets.symmetric(horizontal: horizontalPadding, vertical: 8),
      decoration: BoxDecoration(
        border: Border(top: BorderSide(color: scheme.outlineVariant))),
      child: ValueListenableBuilder<TextEditingValue>(
        valueListenable: controller,
        builder: (context, value, _) {
          final text = value.text;
          final words = text.trim().isEmpty ? 0 : text.trim().split(RegExp(r'\s+')).length;
          return Row(children: [
            Text('$words words', style: style),
            const SizedBox(width: 16),
            Text('${text.length} chars', style: style),
          ]);
        }),
    );
  }
}
