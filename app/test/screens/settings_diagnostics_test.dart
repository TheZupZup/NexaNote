import 'package:flutter_test/flutter_test.dart';
import 'package:nexanote/screens/settings_screen.dart';

void main() {
  group('extractSyncErrorDetails', () {
    test('returns errors list from backend response', () {
      final details = extractSyncErrorDetails({
        'errors': [
          'WebDAV authentication failed',
          'Could not upload notes.json: 401 Unauthorized',
        ],
      });
      expect(details, [
        'WebDAV authentication failed',
        'Could not upload notes.json: 401 Unauthorized',
      ]);
    });

    test('falls back to errors_detail / error_messages', () {
      expect(
        extractSyncErrorDetails({'errors_detail': ['boom']}),
        ['boom'],
      );
      expect(
        extractSyncErrorDetails({'error_messages': ['nope']}),
        ['nope'],
      );
    });

    test('drops null and blank entries', () {
      final details = extractSyncErrorDetails({
        'errors': [null, '', '  ', 'real failure'],
      });
      expect(details, ['real failure']);
    });

    test('returns empty list when no error fields present', () {
      expect(extractSyncErrorDetails({'notes_pulled': 0}), isEmpty);
      expect(extractSyncErrorDetails({'errors': []}), isEmpty);
    });
  });
}
