import 'package:flutter_test/flutter_test.dart';

import 'package:nexanote/services/title_cleaner.dart';

void main() {
  group('cleanRemoteTitle', () {
    test('strips a trailing .md extension', () {
      expect(cleanRemoteTitle('Hello.md'), 'Hello');
      expect(cleanRemoteTitle('Hello.MD'), 'Hello');
    });

    test('strips a hex __<id-prefix> WebDAV slug suffix', () {
      expect(cleanRemoteTitle('Foo__abcd1234'), 'Foo');
      expect(cleanRemoteTitle('Multi Word Title__deadbeef'), 'Multi Word Title');
    });

    test('strips the title-cased Md.<token> form left by the slug parser',
        () {
      expect(
        cleanRemoteTitle('Chaudré De Saucisses__Md.Q2Hhd'),
        'Chaudré De Saucisses',
      );
      expect(cleanRemoteTitle('Foo__md.bar_baz-qux'), 'Foo');
    });

    test('strips combined __<suffix>.md', () {
      expect(cleanRemoteTitle('My Note__abcd1234.md'), 'My Note');
      expect(cleanRemoteTitle('My Note__Md.Q2Hhd.md'), 'My Note');
    });

    test('leaves plain titles alone', () {
      expect(cleanRemoteTitle('Hello World'), 'Hello World');
      // Two underscores are only suspicious when followed by an id-prefix.
      expect(cleanRemoteTitle('Foo__bar baz'), 'Foo__bar baz');
    });

    test('keeps the original when stripping would empty the title', () {
      expect(cleanRemoteTitle('.md'), '.md');
      expect(cleanRemoteTitle('  '), '');
    });

    test('trims whitespace and never returns an artifact-only title', () {
      expect(cleanRemoteTitle('  Hello  '), 'Hello');
      expect(cleanRemoteTitle('Hello.md   '), 'Hello');
    });
  });
}
