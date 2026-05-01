/// A single captured point in a handwritten stroke.
///
/// Mirrors the Python Point model in nexanote/models/note.py.
/// Stored in the stroke_points table, one row per point.
class StrokePoint {
  final double x;
  final double y;

  /// Stylus pressure in the range 0.0–1.0.
  final double pressure;

  /// Milliseconds elapsed since the stroke began.
  final int timestampMs;

  const StrokePoint({
    required this.x,
    required this.y,
    this.pressure = 0.5,
    this.timestampMs = 0,
  });

  factory StrokePoint.fromMap(Map<String, dynamic> map) {
    return StrokePoint(
      x: (map['x'] as num).toDouble(),
      y: (map['y'] as num).toDouble(),
      pressure: (map['pressure'] as num?)?.toDouble() ?? 0.5,
      timestampMs: (map['timestamp_ms'] as int?) ?? 0,
    );
  }

  /// Returns a row for the stroke_points table.
  /// [seq] is the zero-based index of this point within the stroke.
  Map<String, dynamic> toMap(String strokeId, int seq) {
    return {
      'stroke_id': strokeId,
      'x': x,
      'y': y,
      'pressure': pressure,
      'timestamp_ms': timestampMs,
      'seq': seq,
    };
  }

  @override
  String toString() =>
      'StrokePoint(x: $x, y: $y, pressure: $pressure, ts: $timestampMs)';
}
