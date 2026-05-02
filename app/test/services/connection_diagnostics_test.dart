import 'package:flutter_test/flutter_test.dart';

import 'package:nexanote/services/connection_diagnostics.dart';

void main() {
  group('ConnectionDiagnostics.format', () {
    test('includes server URL, timestamp, and error message', () {
      final diag = ConnectionDiagnostics.capture(
        serverUrl: 'http://127.0.0.1:8766',
        errorMessage: 'SocketException: connection refused',
        appVersion: '0.1.0+1',
        now: DateTime.utc(2026, 5, 2, 12, 0, 0),
        platformOverride: 'linux 6.18.5',
        formFactorOverride: 'desktop',
      );
      final text = diag.format();

      expect(text, contains('http://127.0.0.1:8766'));
      expect(text, contains('2026-05-02T12:00:00.000Z'));
      expect(text, contains('SocketException: connection refused'));
      expect(text, contains('linux 6.18.5'));
      expect(text, contains('desktop'));
      expect(text, contains('0.1.0+1'));
      expect(text, contains('nexanote.sh'));
    });

    test('falls back to a placeholder when no error message is given', () {
      final diag = ConnectionDiagnostics.capture(
        serverUrl: 'http://localhost:8766',
        platformOverride: 'test',
        formFactorOverride: 'test',
        now: DateTime.utc(2026, 1, 1),
      );
      expect(diag.format(), contains('Cannot reach the server'));
    });

    test('does not include sensitive fields', () {
      final diag = ConnectionDiagnostics.capture(
        serverUrl: 'http://127.0.0.1:8766',
        errorMessage: 'boom',
        appVersion: '0.1.0',
        platformOverride: 'test',
        formFactorOverride: 'test',
        now: DateTime.utc(2026, 1, 1),
      );
      final text = diag.format().toLowerCase();

      expect(text, isNot(contains('password')));
      expect(text, isNot(contains('token')));
      expect(text, isNot(contains('webdav')));
      expect(text, isNot(contains('authorization')));
      expect(text, isNot(contains('cookie')));
    });
  });
}
