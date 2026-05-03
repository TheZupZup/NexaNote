import 'package:flutter_test/flutter_test.dart';

import 'package:nexanote/services/server_url.dart';

void main() {
  group('ServerUrl.parse', () {
    test('accepts an http LAN URL with port', () {
      final url = ServerUrl.parse('http://192.168.1.10:8766');
      expect(url.value, 'http://192.168.1.10:8766');
    });

    test('accepts an https domain URL', () {
      final url = ServerUrl.parse('https://nexanote.example.com');
      expect(url.value, 'https://nexanote.example.com');
    });

    test('accepts an https domain URL with a path prefix (reverse proxy)', () {
      final url = ServerUrl.parse('https://example.com/nexanote');
      expect(url.value, 'https://example.com/nexanote');
    });

    test('preserves loopback URLs (no localhost requirement either way)', () {
      expect(
        ServerUrl.parse('http://127.0.0.1:8766').value,
        'http://127.0.0.1:8766',
      );
      expect(
        ServerUrl.parse('http://localhost:8766').value,
        'http://localhost:8766',
      );
    });

    test('trims whitespace and trailing slashes', () {
      expect(
        ServerUrl.parse('  https://nexanote.example.com/  ').value,
        'https://nexanote.example.com',
      );
      expect(
        ServerUrl.parse('http://192.168.1.10:8766///').value,
        'http://192.168.1.10:8766',
      );
    });

    test('rejects blank input', () {
      expect(() => ServerUrl.parse(''), throwsFormatException);
      expect(() => ServerUrl.parse('   '), throwsFormatException);
    });

    test('rejects URLs without an http(s) scheme', () {
      expect(() => ServerUrl.parse('192.168.1.10:8766'), throwsFormatException);
      expect(() => ServerUrl.parse('ftp://example.com'), throwsFormatException);
      expect(
        () => ServerUrl.parse('javascript:alert(1)'),
        throwsFormatException,
      );
    });

    test('rejects URLs that are missing a host', () {
      expect(() => ServerUrl.parse('http://'), throwsFormatException);
      expect(() => ServerUrl.parse('https:///path'), throwsFormatException);
    });

    test('error message names a valid example so the user can recover', () {
      try {
        ServerUrl.parse('');
        fail('expected FormatException');
      } on FormatException catch (e) {
        expect(e.message, contains('http'));
      }
    });

    test('does not hardcode a specific domain in error messages', () {
      try {
        ServerUrl.parse('not a url');
        fail('expected FormatException');
      } on FormatException catch (e) {
        expect(e.message.toLowerCase(), isNot(contains('nexanote.example')));
      }
    });
  });

  group('ServerUrl.tryParse', () {
    test('returns the normalized string for valid input', () {
      expect(
        ServerUrl.tryParse('https://nexanote.example.com/'),
        'https://nexanote.example.com',
      );
    });

    test('returns null for invalid input', () {
      expect(ServerUrl.tryParse(''), isNull);
      expect(ServerUrl.tryParse('not a url'), isNull);
      expect(ServerUrl.tryParse('ftp://example.com'), isNull);
    });
  });
}
