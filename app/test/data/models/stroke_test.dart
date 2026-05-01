import 'package:flutter_test/flutter_test.dart';

import 'package:nexanote/data/models/stroke.dart';
import 'package:nexanote/data/models/point.dart';

void main() {
  final _ts = DateTime.utc(2024, 6, 1, 9, 0);

  group('StrokePoint', () {
    test('fromMap reads all fields', () {
      final map = <String, dynamic>{
        'x': 12.5,
        'y': 34.0,
        'pressure': 0.75,
        'timestamp_ms': 250,
      };
      final pt = StrokePoint.fromMap(map);
      expect(pt.x, 12.5);
      expect(pt.y, 34.0);
      expect(pt.pressure, 0.75);
      expect(pt.timestampMs, 250);
    });

    test('fromMap uses defaults when optional fields are absent', () {
      final pt = StrokePoint.fromMap({'x': 1.0, 'y': 2.0});
      expect(pt.pressure, 0.5);
      expect(pt.timestampMs, 0);
    });

    test('toMap includes stroke_id and seq', () {
      final pt = const StrokePoint(x: 5.0, y: 10.0, pressure: 0.8, timestampMs: 100);
      final map = pt.toMap('stroke-99', 3);
      expect(map['stroke_id'], 'stroke-99');
      expect(map['seq'], 3);
      expect(map['x'], 5.0);
      expect(map['pressure'], 0.8);
    });
  });

  group('Stroke', () {
    final points = [
      const StrokePoint(x: 0.0, y: 0.0, pressure: 0.5, timestampMs: 0),
      const StrokePoint(x: 10.0, y: 5.0, pressure: 0.8, timestampMs: 16),
    ];

    test('toMap / fromMap round-trip preserves metadata', () {
      final stroke = Stroke(
        id: 'stroke-1',
        noteId: 'note-1',
        color: '#0000ff',
        width: 3.5,
        tool: 'highlighter',
        createdAt: _ts,
        points: points,
      );

      final map = stroke.toMap();
      // Points are NOT in toMap — they are stored separately.
      expect(map.containsKey('points'), isFalse);

      final restored = Stroke.fromMap(map, points: points);
      expect(restored.id, stroke.id);
      expect(restored.noteId, stroke.noteId);
      expect(restored.color, '#0000ff');
      expect(restored.width, 3.5);
      expect(restored.tool, 'highlighter');
      expect(restored.createdAt, _ts);
      expect(restored.points.length, 2);
    });

    test('fromMap uses default color and tool when absent', () {
      final map = <String, dynamic>{
        'id': 'stroke-2',
        'note_id': 'note-1',
        'created_at': _ts.toIso8601String(),
      };
      final stroke = Stroke.fromMap(map);
      expect(stroke.color, '#000000');
      expect(stroke.tool, 'pen');
      expect(stroke.width, 2.0);
    });

    test('copyWith changes only specified fields', () {
      final stroke = Stroke(
        id: 'stroke-3',
        noteId: 'note-1',
        createdAt: _ts,
      );
      final updated = stroke.copyWith(color: '#ff0000', width: 5.0);
      expect(updated.id, 'stroke-3');
      expect(updated.noteId, 'note-1');
      expect(updated.color, '#ff0000');
      expect(updated.width, 5.0);
    });

    test('toMap does not include points column', () {
      final stroke = Stroke(
        id: 'stroke-4',
        noteId: 'note-1',
        createdAt: _ts,
        points: points,
      );
      final map = stroke.toMap();
      expect(map.keys, containsAll(['id', 'note_id', 'color', 'width', 'tool', 'created_at']));
      expect(map.containsKey('points'), isFalse);
    });
  });
}
