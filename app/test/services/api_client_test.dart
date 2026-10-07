import 'dart:async';

import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

import 'package:nexanote/services/api_client.dart';

void main() {
  ApiClient clientReturning(int status, [String body = '']) => ApiClient(
        baseUrl: 'http://backend.test',
        httpClient: MockClient((_) async => http.Response(body, status)),
      );

  group('writes fail loudly when the server refuses them', () {
    for (final status in [400, 404, 413, 500, 502]) {
      test('savePageText throws on HTTP $status', () async {
        await expectLater(clientReturning(status).savePageText('n', 1, 'x'),
            throwsA(isA<ApiException>()));
      });

      test('savePageInk throws on HTTP $status', () async {
        await expectLater(clientReturning(status).savePageInk('n', 1, const []),
            throwsA(isA<ApiException>()));
      });

      if (status != 404) {
        test('deleteNote throws on HTTP $status', () async {
          await expectLater(clientReturning(status).deleteNote('n'),
              throwsA(isA<ApiException>()));
        });

        test('deleteNotebook throws on HTTP $status', () async {
          await expectLater(clientReturning(status).deleteNotebook('nb'),
              throwsA(isA<ApiException>()));
        });
      }
    }
  });

  test('successful writes complete', () async {
    await clientReturning(200, '{}').savePageText('n', 1, 'x');
    await clientReturning(200, '{}').savePageInk('n', 1, const []);
    await clientReturning(204).deleteNote('n');
    await clientReturning(204).deleteNotebook('nb');
  });

  test('deleting something that is already gone (404) is not an error',
      () async {
    await clientReturning(404).deleteNote('n');
    await clientReturning(404).deleteNotebook('nb');
  });

  testWidgets('a backend that never answers times out instead of hanging',
      (tester) async {
    final client = ApiClient(
      baseUrl: 'http://backend.test',
      httpClient: MockClient((_) => Completer<http.Response>().future),
    );
    Object? error;
    client.getNotes().catchError((Object e) {
      error = e;
      return <Note>[];
    });

    await tester.pump(ApiClient.requestTimeout - const Duration(seconds: 1));
    expect(error, isNull);
    await tester.pump(const Duration(seconds: 2));
    expect(error, isA<ApiException>());
  });

  testWidgets('saves get the longer upload timeout', (tester) async {
    final client = ApiClient(
      baseUrl: 'http://backend.test',
      httpClient: MockClient((_) => Completer<http.Response>().future),
    );
    Object? error;
    client.savePageInk('n', 1, const []).catchError((Object e) => error = e);

    // Still uploading well past the normal request timeout...
    await tester.pump(ApiClient.requestTimeout * 2);
    expect(error, isNull);
    // ...but it does not hang forever either.
    await tester.pump(ApiClient.uploadTimeout);
    expect(error, isA<ApiException>());
  });
}
