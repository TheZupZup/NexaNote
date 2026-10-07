import '../data/models/point.dart';
import '../data/models/stroke.dart';

/// Converts an editor/wire ink stroke (`{id,color,width,tool,points:[…]}`) into
/// the local [Stroke] model for SQLite persistence. The editor emits point
/// timestamps under `ts`; the local model stores them as `timestampMs`.
/// Tolerant of missing fields so a malformed stroke degrades gracefully rather
/// than throwing and losing the whole drawing.
Stroke strokeFromInkJson(
  String noteId,
  Map<String, dynamic> json,
  DateTime createdAt,
) {
  final rawPoints = (json['points'] as List?) ?? const [];
  return Stroke(
    id: (json['id'] as String?) ??
        DateTime.now().microsecondsSinceEpoch.toString(),
    noteId: noteId,
    color: (json['color'] as String?) ?? '#000000',
    width: (json['width'] as num?)?.toDouble() ?? 2.0,
    tool: (json['tool'] as String?) ?? 'pen',
    createdAt: createdAt,
    points: [
      for (final p in rawPoints)
        if (p is Map)
          StrokePoint(
            x: (p['x'] as num?)?.toDouble() ?? 0,
            y: (p['y'] as num?)?.toDouble() ?? 0,
            pressure: (p['pressure'] as num?)?.toDouble() ?? 0.5,
            timestampMs: (p['ts'] as num?)?.toInt() ?? 0,
          ),
    ],
  );
}

/// Inverse of [strokeFromInkJson]: renders a local [Stroke] back into the
/// editor/wire shape so the ink canvas can replay a saved drawing.
Map<String, dynamic> inkJsonFromStroke(Stroke stroke) => {
      'id': stroke.id,
      'color': stroke.color,
      'width': stroke.width,
      'tool': stroke.tool,
      'points': [
        for (final p in stroke.points)
          {
            'x': p.x,
            'y': p.y,
            'pressure': p.pressure,
            'ts': p.timestampMs,
          },
      ],
    };
