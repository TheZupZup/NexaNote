// lib/services/server_url.dart
//
// Parses and normalizes a user-entered backend URL. Accepts http and https
// to any host — LAN IPs, mDNS hostnames, public domains, reverse proxies,
// Cloudflare Tunnel — and rejects blank or malformed input with a clear,
// user-facing message.

/// Path suffix appended to a unified backend base URL to reach the WebDAV
/// server when the deployment is fronted by a reverse proxy that routes
/// `/webdav` to the WebDAV process.
const String kWebdavPathSuffix = '/webdav';

class ServerUrl {
  /// The normalized URL string. Whitespace and trailing slashes are removed;
  /// the rest of the user's input is preserved as-typed.
  final String value;

  const ServerUrl._(this.value);

  /// The REST API URL derived from this base URL. Identity for the unified
  /// deployment — the API is served at the root of the base URL.
  String get apiUrl => value;

  /// The WebDAV URL derived from this base URL by appending [kWebdavPathSuffix].
  /// Use this when the backend is fronted by a reverse proxy that routes
  /// `/webdav` to the WebDAV process. For legacy two-port deployments
  /// (`:8765` for WebDAV, `:8766` for API) the user can still enter the
  /// WebDAV URL directly as an override.
  String get webdavUrl => deriveWebdavUrl(value);

  /// Returns `base + "/webdav"`, taking care not to double-append the suffix
  /// if it's already present. Trailing slashes on [base] are tolerated.
  static String deriveWebdavUrl(String base) {
    final trimmed = _stripTrailingSlashes(base.trim());
    if (trimmed.toLowerCase().endsWith(kWebdavPathSuffix)) {
      return trimmed;
    }
    return '$trimmed$kWebdavPathSuffix';
  }

  /// The URL scheme in lowercase — `http` or `https`.
  String get scheme => Uri.parse(value).scheme.toLowerCase();

  /// The URL host (lowercased), or an empty string if missing.
  String get host => Uri.parse(value).host.toLowerCase();

  /// Whether this URL uses HTTPS.
  bool get isSecure => scheme == 'https';

  /// Whether the host is on the local machine or a private network. Covers
  /// IPv4 loopback (127.0.0.0/8), private ranges (10/8, 172.16/12,
  /// 192.168/16), link-local (169.254/16), the IPv6 loopback (::1),
  /// IPv6 unique-local (fc00::/7) and link-local (fe80::/10), the
  /// `localhost` hostname, and mDNS `*.local` names. Used to decide whether
  /// plain HTTP is acceptable.
  bool get isLocalNetwork => isLocalHost(host);

  /// True for non-local HTTP URLs — i.e. remote connections that would send
  /// traffic over cleartext. The UI uses this to nudge the user toward HTTPS
  /// without forcing it.
  bool get isInsecureRemote => !isSecure && !isLocalNetwork;

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

  /// Returns true if [host] refers to the local machine or a private LAN.
  /// Exposed for callers that want to classify a host string directly,
  /// without first parsing a full URL.
  static bool isLocalHost(String host) {
    final h = host.toLowerCase();
    if (h.isEmpty) return false;
    if (h == 'localhost' || h.endsWith('.local')) return true;
    if (h == '::1') return true;
    // IPv6 unique-local fc00::/7 (fc.. / fd..) and link-local fe80::/10.
    // Gate on the presence of an IPv6 separator so a regular DNS hostname
    // like `fcommerce.example.com` or `feb-foo.net` isn't mistaken for a
    // local IPv6 literal — that misclassification used to suppress the
    // HTTPS warning for legitimately public domains starting with those
    // letters.
    if (h.contains(':')) {
      if (h.startsWith('fc') || h.startsWith('fd')) return true;
      if (h.startsWith('fe8') ||
          h.startsWith('fe9') ||
          h.startsWith('fea') ||
          h.startsWith('feb')) {
        return true;
      }
    }
    final parts = h.split('.');
    if (parts.length != 4) return false;
    final octets = <int>[];
    for (final part in parts) {
      final n = int.tryParse(part);
      if (n == null || n < 0 || n > 255) return false;
      octets.add(n);
    }
    final a = octets[0];
    final b = octets[1];
    if (a == 127) return true; // 127.0.0.0/8 loopback
    if (a == 10) return true; // 10.0.0.0/8
    if (a == 192 && b == 168) return true; // 192.168.0.0/16
    if (a == 172 && b >= 16 && b <= 31) return true; // 172.16.0.0/12
    if (a == 169 && b == 254) return true; // 169.254.0.0/16 link-local
    return false;
  }

  @override
  String toString() => value;
}
