// lib/services/connection_diagnostics.dart
//
// Builds a developer-friendly, copy/paste-able diagnostic summary used by the
// ConnectScreen when the backend cannot be reached. The output is intentionally
// scoped to non-sensitive fields: no credentials, tokens, WebDAV settings, or
// note content ever leak in here.

import 'dart:io' show Platform;

class ConnectionDiagnostics {
  final String serverUrl;
  final String? errorMessage;
  final DateTime timestamp;
  final String platform;
  final String formFactor;
  final String? appVersion;

  const ConnectionDiagnostics({
    required this.serverUrl,
    required this.errorMessage,
    required this.timestamp,
    required this.platform,
    required this.formFactor,
    required this.appVersion,
  });

  factory ConnectionDiagnostics.capture({
    required String serverUrl,
    String? errorMessage,
    String? appVersion,
    DateTime? now,
    String? platformOverride,
    String? formFactorOverride,
  }) {
    return ConnectionDiagnostics(
      serverUrl: serverUrl,
      errorMessage: errorMessage,
      timestamp: now ?? DateTime.now(),
      platform: platformOverride ?? _detectPlatform(),
      formFactor: formFactorOverride ?? _detectFormFactor(),
      appVersion: appVersion,
    );
  }

  static String _detectPlatform() {
    try {
      return '${Platform.operatingSystem} ${Platform.operatingSystemVersion}';
    } catch (_) {
      return 'unknown';
    }
  }

  static String _detectFormFactor() {
    try {
      if (Platform.isAndroid || Platform.isIOS) return 'mobile';
      if (Platform.isLinux || Platform.isMacOS || Platform.isWindows) {
        return 'desktop';
      }
    } catch (_) {}
    return 'unknown';
  }

  String format() {
    final buf = StringBuffer()
      ..writeln('NexaNote connection diagnostics')
      ..writeln('Timestamp: ${timestamp.toUtc().toIso8601String()}')
      ..writeln('Server URL: $serverUrl')
      ..writeln('Platform: $platform')
      ..writeln('Form factor: $formFactor')
      ..writeln('App version: ${appVersion ?? 'unknown'}')
      ..writeln('Error: ${errorMessage ?? 'Cannot reach the server'}')
      ..writeln('Suggested: bash nexanote.sh  (or: python main.py)');
    return buf.toString();
  }
}
