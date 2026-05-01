import 'point.dart';

/// A single handwritten ink stroke belonging to a note.
///
/// A stroke is the unit drawn between pen-down and pen-up events.
/// Linked to a note by [noteId]. Points are stored separately in the
/// stroke_points table and populated on load via [NoteRepository].
///
/// Mirrors InkStroke in nexanote/models/note.py.
class Stroke {
  final String id;
  final String noteId;
  final String color;
  final double width;

  /// Tool type: 'pen' | 'highlighter' | 'eraser'
  final String tool;

  final DateTime createdAt;

  /// Ordered list of captured points. Empty until loaded from the DB.
  final List<StrokePoint> points;

  const Stroke({
    required this.id,
    required this.noteId,
    this.color = '#000000',
    this.width = 2.0,
    this.tool = 'pen',
    required this.createdAt,
    this.points = const [],
  });

  factory Stroke.fromMap(
    Map<String, dynamic> map, {
    List<StrokePoint> points = const [],
  }) {
    return Stroke(
      id: map['id'] as String,
      noteId: map['note_id'] as String,
      color: (map['color'] as String?) ?? '#000000',
      width: (map['width'] as num?)?.toDouble() ?? 2.0,
      tool: (map['tool'] as String?) ?? 'pen',
      createdAt: DateTime.parse(map['created_at'] as String),
      points: points,
    );
  }

  /// Returns a row for the strokes table (excludes points).
  Map<String, dynamic> toMap() {
    return {
      'id': id,
      'note_id': noteId,
      'color': color,
      'width': width,
      'tool': tool,
      'created_at': createdAt.toIso8601String(),
    };
  }

  Stroke copyWith({
    String? color,
    double? width,
    String? tool,
    List<StrokePoint>? points,
  }) {
    return Stroke(
      id: id,
      noteId: noteId,
      color: color ?? this.color,
      width: width ?? this.width,
      tool: tool ?? this.tool,
      createdAt: createdAt,
      points: points ?? this.points,
    );
  }

  @override
  String toString() =>
      'Stroke(id: $id, tool: $tool, points: ${points.length})';
}
