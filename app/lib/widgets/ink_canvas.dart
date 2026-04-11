import 'dart:ui' show PointMode;
// lib/widgets/ink_canvas.dart
// Canvas d'écriture manuscrite — support stylet, souris, doigt
// Pression, gomme, surligneur, undo/redo

import 'package:flutter/material.dart';
import 'package:flutter/gestures.dart';

// ----------------------------------------------------------------
// Modèles
// ----------------------------------------------------------------

class InkPoint {
  final double x;
  final double y;
  final double pressure;
  final int timestampMs;

  const InkPoint({
    required this.x,
    required this.y,
    this.pressure = 0.5,
    this.timestampMs = 0,
  });

  Map<String, dynamic> toJson() => {
        'x': x,
        'y': y,
        'pressure': pressure,
        'ts': timestampMs,
      };
}

enum InkTool { pen, highlighter, eraser }

class InkStrokeData {
  final String id;
  final List<InkPoint> points;
  final Color color;
  final double width;
  final InkTool tool;

  InkStrokeData({
    required this.id,
    required this.points,
    required this.color,
    required this.width,
    required this.tool,
  });

  Map<String, dynamic> toJson() => {
        'id': id,
        'color':
            '#${color.value.toRadixString(16).padLeft(8, '0').substring(2)}',
        'width': width,
        'tool': tool.name,
        'points': points.map((p) => p.toJson()).toList(),
      };

  static InkStrokeData fromJson(Map<String, dynamic> j) {
    final colorHex = (j['color'] as String).replaceAll('#', '');
    return InkStrokeData(
      id: j['id'],
      color: Color(int.parse('FF$colorHex', radix: 16)),
      width: (j['width'] as num).toDouble(),
      tool: InkTool.values.firstWhere(
        (t) => t.name == j['tool'],
        orElse: () => InkTool.pen,
      ),
      points: (j['points'] as List)
          .map((p) => InkPoint(
                x: (p['x'] as num).toDouble(),
                y: (p['y'] as num).toDouble(),
                pressure: (p['pressure'] as num?)?.toDouble() ?? 0.5,
                timestampMs: (p['ts'] as num?)?.toInt() ?? 0,
              ))
          .toList(),
    );
  }
}

// ----------------------------------------------------------------
// Painter
// ----------------------------------------------------------------

class _InkPainter extends CustomPainter {
  final List<InkStrokeData> strokes;
  final InkStrokeData? currentStroke;
  final String template;

  _InkPainter({
    required this.strokes,
    this.currentStroke,
    required this.template,
  });

  @override
  void paint(Canvas canvas, Size size) {
    // Fond de page
    canvas.drawRect(
      Rect.fromLTWH(0, 0, size.width, size.height),
      Paint()..color = Colors.white,
    );

    // Template de page
    _drawTemplate(canvas, size);

    // Strokes sauvegardés
    for (final stroke in strokes) {
      _drawStroke(canvas, stroke);
    }

    // Stroke en cours
    if (currentStroke != null) {
      _drawStroke(canvas, currentStroke!);
    }
  }

  void _drawTemplate(Canvas canvas, Size size) {
    final paint = Paint()
      ..color = const Color(0xFFDDDDFF).withOpacity(0.5)
      ..strokeWidth = 0.5;

    switch (template) {
      case 'lined':
        const spacing = 40.0;
        for (double y = spacing; y < size.height; y += spacing) {
          canvas.drawLine(Offset(0, y), Offset(size.width, y), paint);
        }
        break;
      case 'grid':
        const spacing = 40.0;
        for (double y = spacing; y < size.height; y += spacing) {
          canvas.drawLine(Offset(0, y), Offset(size.width, y), paint);
        }
        for (double x = spacing; x < size.width; x += spacing) {
          canvas.drawLine(Offset(x, 0), Offset(x, size.height), paint);
        }
        break;
      case 'dotted':
        const spacing = 40.0;
        final dotPaint = Paint()
          ..color = const Color(0xFFAAAACC).withOpacity(0.5)
          ..strokeWidth = 1.5
          ..strokeCap = StrokeCap.round;
        for (double y = spacing; y < size.height; y += spacing) {
          for (double x = spacing; x < size.width; x += spacing) {
            canvas.drawPoints(
              PointMode.points,
              [Offset(x, y)],
              dotPaint,
            );
          }
        }
        break;
      default:
        break;
    }
  }

  void _drawStroke(Canvas canvas, InkStrokeData stroke) {
    if (stroke.points.length < 2) return;

    if (stroke.tool == InkTool.eraser) {
      _drawEraserStroke(canvas, stroke);
      return;
    }

    final isHighlighter = stroke.tool == InkTool.highlighter;

    for (int i = 0; i < stroke.points.length - 1; i++) {
      final p1 = stroke.points[i];
      final p2 = stroke.points[i + 1];

      // Largeur variable selon la pression
      final pressure = (p1.pressure + p2.pressure) / 2;
      final w = stroke.width * (isHighlighter ? 1.0 : pressure * 1.5 + 0.5);

      final paint = Paint()
        ..color = isHighlighter
            ? stroke.color.withOpacity(0.35)
            : stroke.color
        ..strokeWidth = w
        ..strokeCap = StrokeCap.round
        ..strokeJoin = StrokeJoin.round
        ..style = PaintingStyle.stroke;

      if (isHighlighter) {
        paint.blendMode = BlendMode.multiply;
      }

      // Courbe de Bézier pour un rendu fluide
      if (i == 0 || i == stroke.points.length - 2) {
        canvas.drawLine(
          Offset(p1.x, p1.y),
          Offset(p2.x, p2.y),
          paint,
        );
      } else {
        final p0 = stroke.points[i - 1];
        final cp = Offset(
          (p1.x + p2.x) / 2,
          (p1.y + p2.y) / 2,
        );
        final path = Path()
          ..moveTo((p0.x + p1.x) / 2, (p0.y + p1.y) / 2)
          ..quadraticBezierTo(p1.x, p1.y, cp.dx, cp.dy);
        canvas.drawPath(path, paint);
      }
    }
  }

  void _drawEraserStroke(Canvas canvas, InkStrokeData stroke) {
    final paint = Paint()
      ..color = Colors.white
      ..strokeWidth = stroke.width * 4
      ..strokeCap = StrokeCap.round
      ..style = PaintingStyle.stroke;

    for (int i = 0; i < stroke.points.length - 1; i++) {
      canvas.drawLine(
        Offset(stroke.points[i].x, stroke.points[i].y),
        Offset(stroke.points[i + 1].x, stroke.points[i + 1].y),
        paint,
      );
    }
  }

  @override
  bool shouldRepaint(_InkPainter old) => true;
}

// ----------------------------------------------------------------
// Widget principal
// ----------------------------------------------------------------

class InkCanvas extends StatefulWidget {
  final List<Map<String, dynamic>> initialStrokes;
  final String template;
  final Function(List<Map<String, dynamic>>) onStrokesChanged;

  const InkCanvas({
    super.key,
    required this.initialStrokes,
    required this.template,
    required this.onStrokesChanged,
  });

  @override
  State<InkCanvas> createState() => _InkCanvasState();
}

class _InkCanvasState extends State<InkCanvas> {
  final List<InkStrokeData> _strokes = [];
  final List<List<InkStrokeData>> _undoStack = [];
  InkStrokeData? _currentStroke;

  // Outils
  InkTool _tool = InkTool.pen;
  Color _color = Colors.black;
  double _width = 3.0;

  // Zoom / pan
  double _scale = 1.0;
  Offset _offset = Offset.zero;
  Offset _lastFocalPoint = Offset.zero;

  final List<Color> _colors = [
    Colors.black,
    const Color(0xFF2563EB), // bleu
    const Color(0xFFDC2626), // rouge
    const Color(0xFF16A34A), // vert
    const Color(0xFF9333EA), // violet
    const Color(0xFFEA580C), // orange
    const Color(0xFFDB2777), // rose
  ];

  @override
  void initState() {
    super.initState();
    // Charger les strokes existants
    for (final s in widget.initialStrokes) {
      try {
        _strokes.add(InkStrokeData.fromJson(s));
      } catch (_) {}
    }
  }

  String _newId() =>
      DateTime.now().millisecondsSinceEpoch.toString() +
      (_strokes.length).toString();

  void _startStroke(Offset pos, double pressure) {
    if (_tool == InkTool.eraser) {
      _currentStroke = InkStrokeData(
        id: _newId(),
        points: [InkPoint(x: pos.dx, y: pos.dy, pressure: pressure)],
        color: Colors.white,
        width: _width * 3,
        tool: InkTool.eraser,
      );
    } else {
      _currentStroke = InkStrokeData(
        id: _newId(),
        points: [InkPoint(x: pos.dx, y: pos.dy, pressure: pressure)],
        color: _color,
        width: _width,
        tool: _tool,
      );
    }
    setState(() {});
  }

  void _addPoint(Offset pos, double pressure) {
    if (_currentStroke == null) return;
    final newPoints = List<InkPoint>.from(_currentStroke!.points)
      ..add(InkPoint(
        x: pos.dx,
        y: pos.dy,
        pressure: pressure,
        timestampMs: DateTime.now().millisecondsSinceEpoch,
      ));
    _currentStroke = InkStrokeData(
      id: _currentStroke!.id,
      points: newPoints,
      color: _currentStroke!.color,
      width: _currentStroke!.width,
      tool: _currentStroke!.tool,
    );
    setState(() {});
  }

  void _endStroke() {
    if (_currentStroke == null) return;
    if (_currentStroke!.points.length > 1) {
      _undoStack.add(List.from(_strokes));
      _strokes.add(_currentStroke!);
      widget.onStrokesChanged(_strokes.map((s) => s.toJson()).toList());
    }
    _currentStroke = null;
    setState(() {});
  }

  void _undo() {
    if (_undoStack.isEmpty) return;
    setState(() {
      _strokes.clear();
      _strokes.addAll(_undoStack.removeLast());
    });
    widget.onStrokesChanged(_strokes.map((s) => s.toJson()).toList());
  }

  void _clear() {
    _undoStack.add(List.from(_strokes));
    setState(() => _strokes.clear());
    widget.onStrokesChanged([]);
  }

  Offset _toCanvasPos(Offset globalPos) {
    final box = context.findRenderObject() as RenderBox?;
    if (box == null) return globalPos;
    final local = box.globalToLocal(globalPos);
    return Offset(
      (local.dx - _offset.dx) / _scale,
      (local.dy - _offset.dy) / _scale,
    );
  }

  @override
  Widget build(BuildContext context) {
    return Column(
      children: [
        // ── Toolbar ──────────────────────────────────────────────
        _InkToolbar(
          currentTool: _tool,
          currentColor: _color,
          currentWidth: _width,
          colors: _colors,
          canUndo: _undoStack.isNotEmpty,
          onToolChanged: (t) => setState(() => _tool = t),
          onColorChanged: (c) => setState(() {
            _color = c;
            _tool = InkTool.pen;
          }),
          onWidthChanged: (w) => setState(() => _width = w),
          onUndo: _undo,
          onClear: _clear,
        ),

        // ── Canvas ───────────────────────────────────────────────
        Expanded(
          child: ClipRect(
            child: Listener(
              onPointerDown: (e) {
                if (e.kind == PointerDeviceKind.stylus ||
                    e.kind == PointerDeviceKind.mouse ||
                    e.kind == PointerDeviceKind.touch) {
                  final pressure = e.pressure > 0 ? e.pressure : 0.5;
                  _startStroke(_toCanvasPos(e.position), pressure);
                }
              },
              onPointerMove: (e) {
                final pressure = e.pressure > 0 ? e.pressure : 0.5;
                _addPoint(_toCanvasPos(e.position), pressure);
              },
              onPointerUp: (_) => _endStroke(),
              onPointerCancel: (_) => _endStroke(),
              child: GestureDetector(
                // Zoom avec pinch
                onScaleStart: (d) {
                  _lastFocalPoint = d.focalPoint;
                },
                onScaleUpdate: (d) {
                  if (d.pointerCount == 2) {
                    setState(() {
                      _scale = (_scale * d.scale).clamp(0.5, 5.0);
                      final delta = d.focalPoint - _lastFocalPoint;
                      _offset += delta;
                      _lastFocalPoint = d.focalPoint;
                    });
                  }
                },
                child: Transform(
                  transform: Matrix4.identity()
                    ..translate(_offset.dx, _offset.dy)
                    ..scale(_scale),
                  child: CustomPaint(
                    painter: _InkPainter(
                      strokes: _strokes,
                      currentStroke: _currentStroke,
                      template: widget.template,
                    ),
                    size: const Size(1404, 1872),
                  ),
                ),
              ),
            ),
          ),
        ),
      ],
    );
  }
}

// ----------------------------------------------------------------
// Toolbar
// ----------------------------------------------------------------

class _InkToolbar extends StatelessWidget {
  final InkTool currentTool;
  final Color currentColor;
  final double currentWidth;
  final List<Color> colors;
  final bool canUndo;
  final ValueChanged<InkTool> onToolChanged;
  final ValueChanged<Color> onColorChanged;
  final ValueChanged<double> onWidthChanged;
  final VoidCallback onUndo;
  final VoidCallback onClear;

  const _InkToolbar({
    required this.currentTool,
    required this.currentColor,
    required this.currentWidth,
    required this.colors,
    required this.canUndo,
    required this.onToolChanged,
    required this.onColorChanged,
    required this.onWidthChanged,
    required this.onUndo,
    required this.onClear,
  });

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;

    return Container(
      height: 52,
      padding: const EdgeInsets.symmetric(horizontal: 12),
      decoration: BoxDecoration(
        color: scheme.surface,
        border: Border(bottom: BorderSide(color: scheme.outlineVariant)),
      ),
      child: Row(
        children: [
          // Outils
          _ToolBtn(
            icon: Icons.edit,
            label: 'Pen',
            selected: currentTool == InkTool.pen,
            onTap: () => onToolChanged(InkTool.pen),
          ),
          _ToolBtn(
            icon: Icons.format_color_fill,
            label: 'Highlighter',
            selected: currentTool == InkTool.highlighter,
            onTap: () => onToolChanged(InkTool.highlighter),
          ),
          _ToolBtn(
            icon: Icons.auto_fix_normal,
            label: 'Eraser',
            selected: currentTool == InkTool.eraser,
            onTap: () => onToolChanged(InkTool.eraser),
          ),

          const VerticalDivider(width: 16, indent: 8, endIndent: 8),

          // Couleurs
          ...colors.map((c) => GestureDetector(
                onTap: () => onColorChanged(c),
                child: Container(
                  width: 22,
                  height: 22,
                  margin: const EdgeInsets.symmetric(horizontal: 3),
                  decoration: BoxDecoration(
                    color: c,
                    shape: BoxShape.circle,
                    border: Border.all(
                      color: currentColor == c
                          ? scheme.primary
                          : Colors.transparent,
                      width: 2.5,
                    ),
                  ),
                ),
              )),

          const VerticalDivider(width: 16, indent: 8, endIndent: 8),

          // Épaisseur
          const Icon(Icons.line_weight, size: 16),
          SizedBox(
            width: 80,
            child: Slider(
              value: currentWidth,
              min: 1.0,
              max: 12.0,
              divisions: 11,
              onChanged: onWidthChanged,
              activeColor: const Color(0xFF6366F1),
            ),
          ),

          const Spacer(),

          // Undo / Clear
          IconButton(
            icon: const Icon(Icons.undo, size: 20),
            onPressed: canUndo ? onUndo : null,
            tooltip: 'Undo',
            visualDensity: VisualDensity.compact,
          ),
          IconButton(
            icon: const Icon(Icons.delete_outline, size: 20),
            onPressed: onClear,
            tooltip: 'Clear page',
            visualDensity: VisualDensity.compact,
          ),
        ],
      ),
    );
  }
}

class _ToolBtn extends StatelessWidget {
  final IconData icon;
  final String label;
  final bool selected;
  final VoidCallback onTap;

  const _ToolBtn({
    required this.icon,
    required this.label,
    required this.selected,
    required this.onTap,
  });

  @override
  Widget build(BuildContext context) {
    return Tooltip(
      message: label,
      child: InkWell(
        onTap: onTap,
        borderRadius: BorderRadius.circular(8),
        child: Container(
          padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 6),
          decoration: BoxDecoration(
            color: selected
                ? const Color(0xFF6366F1).withOpacity(0.15)
                : Colors.transparent,
            borderRadius: BorderRadius.circular(8),
          ),
          child: Icon(
            icon,
            size: 20,
            color: selected
                ? const Color(0xFF6366F1)
                : Theme.of(context).colorScheme.onSurface.withOpacity(0.6),
          ),
        ),
      ),
    );
  }
}
