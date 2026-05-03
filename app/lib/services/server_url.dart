// lib/services/server_url.dart
//
// Parses and normalizes a user-entered backend URL. Accepts http and https
// to any host — LAN IPs, mDNS hostnames, public domains, reverse proxies,
// Cloudflare Tunnel — and rejects blank or malformed input with a clear,
// user-facing message.

class ServerUrl {
  /// The normalized URL string. Whitespace and trailing slashes are removed;
  /// the rest of the user's input is preserved as-typed.
  final String value;

  const ServerUrl._(this.value);

  /// Parses [input] and returns a normalized [ServerUrl].
  ///
  /// Throws [FormatException] with a message safe to show to the user when
  /// the URL is blank, uses an unsupported scheme, or is missing a host.
  static ServerUrl parse(String input) {
    final trimmed = input.trim();
    if (trimmed.isEmpty) {
      throw const FormatException(
        'Enter a server URL (e.g. http://192.168.1.10:8766 or '
        'https://nexanote.example.com).',
      );
    }

    final Uri uri;
    try {
      uri = Uri.parse(trimmed);
    } on FormatException {
      throw FormatException(_invalidMessage(trimmed));
    }

    final scheme = uri.scheme.toLowerCase();
    if (scheme != 'http' && scheme != 'https') {
      throw const FormatException(
        'Server URL must start with http:// or https://.',
      );
    }

    if (uri.host.isEmpty) {
      throw const FormatException(
        'Server URL is missing a host. '
        'Try http://192.168.1.10:8766 or https://nexanote.example.com.',
      );
    }

    return ServerUrl._(_stripTrailingSlashes(trimmed));
  }

  /// Returns the normalized URL string on success, or null if [input] is
  /// not a valid server URL.
  static String? tryParse(String input) {
    try {
      return parse(input).value;
    } on FormatException {
      return null;
    }
  }

  static String _invalidMessage(String input) =>
      'Invalid URL: "$input". Use http://host[:port] or https://host.';

  static String _stripTrailingSlashes(String s) {
    var end = s.length;
    while (end > 0 && s.codeUnitAt(end - 1) == 0x2F /* '/' */) {
      end--;
    }
    return s.substring(0, end);
  }

  @override
  String toString() => value;
}
