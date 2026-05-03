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

  group('ServerUrl.isSecure', () {
    test('is true for https URLs', () {
      expect(ServerUrl.parse('https://example.com').isSecure, isTrue);
    });

    test('is false for http URLs (including local)', () {
      expect(ServerUrl.parse('http://example.com').isSecure, isFalse);
      expect(ServerUrl.parse('http://192.168.1.10:8766').isSecure, isFalse);
    });
  });

  group('ServerUrl.isLocalNetwork', () {
    test('is true for the documented private IPv4 ranges', () {
      expect(
        ServerUrl.parse('http://192.168.1.10:8766').isLocalNetwork,
        isTrue,
      );
      expect(ServerUrl.parse('http://10.0.0.5').isLocalNetwork, isTrue);
      expect(ServerUrl.parse('http://10.255.255.254').isLocalNetwork, isTrue);
      expect(ServerUrl.parse('http://172.16.0.1').isLocalNetwork, isTrue);
      expect(ServerUrl.parse('http://172.20.5.5').isLocalNetwork, isTrue);
      expect(ServerUrl.parse('http://172.31.255.254').isLocalNetwork, isTrue);
    });

    test('is false for IPs adjacent to the private ranges', () {
      expect(ServerUrl.parse('http://172.15.0.1').isLocalNetwork, isFalse);
      expect(ServerUrl.parse('http://172.32.0.1').isLocalNetwork, isFalse);
      expect(ServerUrl.parse('http://192.169.1.1').isLocalNetwork, isFalse);
      expect(ServerUrl.parse('http://11.0.0.1').isLocalNetwork, isFalse);
    });

    test('is true for loopback and conventional local hostnames', () {
      expect(ServerUrl.parse('http://127.0.0.1:8766').isLocalNetwork, isTrue);
      expect(ServerUrl.parse('http://127.5.5.5').isLocalNetwork, isTrue);
      expect(ServerUrl.parse('http://localhost:8766').isLocalNetwork, isTrue);
      expect(ServerUrl.parse('http://my-nas.local').isLocalNetwork, isTrue);
    });

    test('is true for IPv4 link-local 169.254/16', () {
      expect(ServerUrl.parse('http://169.254.10.1').isLocalNetwork, isTrue);
    });

    test('is true for IPv6 loopback / unique-local / link-local', () {
      expect(ServerUrl.parse('http://[::1]:8080').isLocalNetwork, isTrue);
      expect(ServerUrl.parse('http://[fc00::1]').isLocalNetwork, isTrue);
      expect(ServerUrl.parse('http://[fd12:3456::1]').isLocalNetwork, isTrue);
      expect(ServerUrl.parse('http://[fe80::1]').isLocalNetwork, isTrue);
    });

    test('is false for public domain names', () {
      expect(
        ServerUrl.parse('https://nexanote.example.com').isLocalNetwork,
        isFalse,
      );
      expect(ServerUrl.parse('http://example.com').isLocalNetwork, isFalse);
    });
  });

  group('ServerUrl.deriveWebdavUrl', () {
    test('appends /webdav to a unified base URL', () {
      expect(
        ServerUrl.deriveWebdavUrl('https://nexanote.example.com'),
        'https://nexanote.example.com/webdav',
      );
      expect(
        ServerUrl.deriveWebdavUrl('http://192.168.1.10:8766'),
        'http://192.168.1.10:8766/webdav',
      );
    });

    test('strips trailing slashes before appending', () {
      expect(
        ServerUrl.deriveWebdavUrl('https://nexanote.example.com/'),
        'https://nexanote.example.com/webdav',
      );
      expect(
        ServerUrl.deriveWebdavUrl('http://127.0.0.1:8766///'),
        'http://127.0.0.1:8766/webdav',
      );
    });

    test('does not double-append /webdav if it is already present', () {
      // Backward compatibility: a user who saved the legacy direct WebDAV
      // URL ("…/webdav") through manual override should not end up with
      // "…/webdav/webdav" after a round-trip.
      expect(
        ServerUrl.deriveWebdavUrl('https://nexanote.example.com/webdav'),
        'https://nexanote.example.com/webdav',
      );
      expect(
        ServerUrl.deriveWebdavUrl('https://nexanote.example.com/webdav/'),
        'https://nexanote.example.com/webdav',
      );
    });

    test('the parsed instance exposes apiUrl and webdavUrl getters', () {
      final url = ServerUrl.parse('https://nexanote.example.com');
      expect(url.apiUrl, 'https://nexanote.example.com');
      expect(url.webdavUrl, 'https://nexanote.example.com/webdav');
    });

    test('preserves a path-prefixed reverse-proxy base URL', () {
      // A user behind a reverse proxy that serves NexaNote under a sub-path
      // (e.g. https://example.com/nexanote → API, /nexanote/webdav → WebDAV)
      // should still get the right WebDAV URL.
      expect(
        ServerUrl.deriveWebdavUrl('https://example.com/nexanote'),
        'https://example.com/nexanote/webdav',
      );
    });
  });

  group('ServerUrl.isInsecureRemote', () {
    test('warns when http is used against a public host', () {
      expect(
        ServerUrl.parse('http://example.com').isInsecureRemote,
        isTrue,
      );
      expect(
        ServerUrl.parse('http://nexanote.example.com:8766').isInsecureRemote,
        isTrue,
      );
    });

    test('does not warn for http against a LAN / loopback / mDNS host', () {
      expect(
        ServerUrl.parse('http://192.168.1.10:8766').isInsecureRemote,
        isFalse,
      );
      expect(
        ServerUrl.parse('http://10.0.0.5').isInsecureRemote,
        isFalse,
      );
      expect(
        ServerUrl.parse('http://172.16.5.5').isInsecureRemote,
        isFalse,
      );
      expect(
        ServerUrl.parse('http://127.0.0.1:8766').isInsecureRemote,
        isFalse,
      );
      expect(
        ServerUrl.parse('http://localhost:8766').isInsecureRemote,
        isFalse,
      );
      expect(
        ServerUrl.parse('http://my-nas.local').isInsecureRemote,
        isFalse,
      );
    });

    test('does not warn for https, regardless of host', () {
      expect(
        ServerUrl.parse('https://nexanote.example.com').isInsecureRemote,
        isFalse,
      );
      expect(
        ServerUrl.parse('https://192.168.1.10:8766').isInsecureRemote,
        isFalse,
      );
    });
  });
}
