// lib/screens/connect_screen.dart
// Écran de connexion au backend — affiché au premier lancement

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:provider/provider.dart';
import '../services/app_state.dart';
import '../services/connection_diagnostics.dart';
import '../services/server_url.dart';

class ConnectScreen extends StatefulWidget {
  const ConnectScreen({super.key});

  @override
  State<ConnectScreen> createState() => _ConnectScreenState();
}

class _ConnectScreenState extends State<ConnectScreen> {
  late final TextEditingController _controller;
  bool _connecting = false;
  bool _enteringOffline = false;
  String? _error;
  bool _showDetails = false;

  @override
  void initState() {
    super.initState();
    // Pre-fill with the URL the user already entered, when there is one.
    // Lets them retry a typo without retyping the whole address, and avoids
    // overwriting a remote URL with the local-loopback default if they ever
    // land back here from Settings → Reconnect.
    final savedUrl = context.read<AppState>().apiUrl;
    _controller = TextEditingController(text: savedUrl);
  }

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
  }

  /// Enters local-only mode. NexaNote is local-first: the user can start
  /// taking notes immediately with no backend. AppState flips into local mode
  /// and notifies, so the root router rebuilds this screen away to HomeScreen.
  Future<void> _useOffline() async {
    final state = context.read<AppState>();
    setState(() { _enteringOffline = true; _error = null; });
    await state.enableLocalMode();
  }

  Future<void> _connect() async {
    setState(() { _connecting = true; _error = null; });

    final String url;
    try {
      url = ServerUrl.parse(_controller.text).value;
    } on FormatException catch (e) {
      setState(() {
        _error = e.message;
        _connecting = false;
      });
      return;
    }

    final state = context.read<AppState>();
    await state.connect(url: url);
    if (mounted && !state.isConnected) {
      setState(() {
        _error = 'Cannot reach $url.\n'
            'Check that the backend is running and reachable from this device.';
        _connecting = false;
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;

    return Scaffold(
      body: Center(
        child: ConstrainedBox(
          constraints: const BoxConstraints(maxWidth: 420),
          child: SingleChildScrollView(
            padding: const EdgeInsets.all(32),
            child: Column(
              mainAxisSize: MainAxisSize.min,
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                // Logo
                Container(
                  width: 64,
                  height: 64,
                  decoration: BoxDecoration(
                    color: const Color(0xFF6366F1),
                    borderRadius: BorderRadius.circular(16),
                  ),
                  child: const Icon(Icons.edit_note, color: Colors.white, size: 40),
                ),
                const SizedBox(height: 24),
                Text('Welcome to NexaNote',
                    style: Theme.of(context).textTheme.headlineSmall?.copyWith(
                          fontWeight: FontWeight.bold,
                        )),
                const SizedBox(height: 8),
                Text(
                  'Take notes right away — they are saved privately on this '
                  'device. Connecting a server is optional and only needed to '
                  'sync across devices.',
                  style: Theme.of(context).textTheme.bodyMedium?.copyWith(
                        color: scheme.onSurface.withOpacity(0.6),
                      ),
                ),
                const SizedBox(height: 24),

                // Primary path — start using the app immediately, no backend.
                SizedBox(
                  width: double.infinity,
                  child: FilledButton.icon(
                    onPressed: (_connecting || _enteringOffline)
                        ? null
                        : _useOffline,
                    icon: _enteringOffline
                        ? const SizedBox(
                            width: 18,
                            height: 18,
                            child: CircularProgressIndicator(
                                strokeWidth: 2, color: Colors.white),
                          )
                        : const Icon(Icons.cloud_off_outlined),
                    label: Text(
                        _enteringOffline ? 'Starting...' : 'Use NexaNote offline'),
                    style: FilledButton.styleFrom(
                      backgroundColor: const Color(0xFF6366F1),
                      padding: const EdgeInsets.symmetric(vertical: 14),
                    ),
                  ),
                ),
                const SizedBox(height: 24),

                // Optional path — connect a backend for WebDAV/NAS sync.
                Row(children: [
                  const Expanded(child: Divider()),
                  Padding(
                    padding: const EdgeInsets.symmetric(horizontal: 12),
                    child: Text('or connect to a server',
                        style: TextStyle(
                            fontSize: 12,
                            color: scheme.onSurface.withOpacity(0.5))),
                  ),
                  const Expanded(child: Divider()),
                ]),
                const SizedBox(height: 20),

                // URL field
                TextField(
                  controller: _controller,
                  decoration: const InputDecoration(
                    labelText: 'Server URL',
                    hintText: 'http://127.0.0.1:8766',
                    prefixIcon: Icon(Icons.dns_outlined),
                  ),
                  onChanged: (_) => setState(() {}),
                  onSubmitted: (_) => _connect(),
                ),
                _SecurityHint(rawUrl: _controller.text),
                const SizedBox(height: 16),

                // Error
                if (_error != null)
                  Container(
                    padding: const EdgeInsets.all(12),
                    decoration: BoxDecoration(
                      color: scheme.errorContainer,
                      borderRadius: BorderRadius.circular(8),
                    ),
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Row(
                          children: [
                            Icon(Icons.warning_amber_rounded,
                                color: scheme.onErrorContainer, size: 18),
                            const SizedBox(width: 8),
                            Expanded(
                              child: Text(
                                _error!,
                                style: TextStyle(
                                    color: scheme.onErrorContainer,
                                    fontSize: 13),
                              ),
                            ),
                          ],
                        ),
                        const SizedBox(height: 8),
                        Row(
                          children: [
                            TextButton.icon(
                              onPressed: _copyDiagnostics,
                              icon: Icon(Icons.copy,
                                  size: 14, color: scheme.onErrorContainer),
                              label: Text('Copy diagnostics',
                                  style: TextStyle(
                                      color: scheme.onErrorContainer,
                                      fontSize: 12)),
                              style: TextButton.styleFrom(
                                padding: const EdgeInsets.symmetric(
                                    horizontal: 8, vertical: 0),
                                minimumSize: const Size(0, 28),
                                tapTargetSize:
                                    MaterialTapTargetSize.shrinkWrap,
                              ),
                            ),
                            TextButton(
                              onPressed: () =>
                                  setState(() => _showDetails = !_showDetails),
                              style: TextButton.styleFrom(
                                padding: const EdgeInsets.symmetric(
                                    horizontal: 8, vertical: 0),
                                minimumSize: const Size(0, 28),
                                tapTargetSize:
                                    MaterialTapTargetSize.shrinkWrap,
                              ),
                              child: Text(
                                  _showDetails
                                      ? 'Hide details'
                                      : 'Show details',
                                  style: TextStyle(
                                      color: scheme.onErrorContainer,
                                      fontSize: 12)),
                            ),
                          ],
                        ),
                        if (_showDetails) ...[
                          const SizedBox(height: 6),
                          SelectableText(
                            _buildDiagnostics().format(),
                            style: TextStyle(
                                fontFamily: 'monospace',
                                fontSize: 11,
                                color: scheme.onErrorContainer),
                          ),
                        ],
                      ],
                    ),
                  ),

                const SizedBox(height: 16),

                // Connect button (optional path)
                SizedBox(
                  width: double.infinity,
                  child: OutlinedButton.icon(
                    onPressed: (_connecting || _enteringOffline)
                        ? null
                        : _connect,
                    icon: _connecting
                        ? const SizedBox(
                            width: 18,
                            height: 18,
                            child: CircularProgressIndicator(strokeWidth: 2),
                          )
                        : const Icon(Icons.arrow_forward),
                    label: Text(
                        _connecting ? 'Connecting...' : 'Connect to server'),
                    style: OutlinedButton.styleFrom(
                      padding: const EdgeInsets.symmetric(vertical: 14),
                    ),
                  ),
                ),

                const SizedBox(height: 24),

                // Help
                Container(
                  padding: const EdgeInsets.all(14),
                  decoration: BoxDecoration(
                    color: scheme.surfaceContainerHighest.withOpacity(0.5),
                    borderRadius: BorderRadius.circular(10),
                  ),
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Row(children: [
                        Icon(Icons.info_outline,
                            size: 16,
                            color: scheme.onSurface.withOpacity(0.5)),
                        const SizedBox(width: 6),
                        Text('How to start the server',
                            style: TextStyle(
                                fontWeight: FontWeight.w600,
                                fontSize: 13,
                                color: scheme.onSurface.withOpacity(0.7))),
                      ]),
                      const SizedBox(height: 8),
                      _code('cd ~/NexaNote'),
                      _code('bash nexanote.sh'),
                      _code('# or: python main.py'),
                    ],
                  ),
                ),
              ],
            ),
          ),
        ),
      ),
    );
  }

  ConnectionDiagnostics _buildDiagnostics() {
    final state = context.read<AppState>();
    final raw = _controller.text;
    return ConnectionDiagnostics.capture(
      serverUrl: ServerUrl.tryParse(raw) ?? raw.trim(),
      errorMessage: state.lastConnectError,
    );
  }

  Future<void> _copyDiagnostics() async {
    final text = _buildDiagnostics().format();
    await Clipboard.setData(ClipboardData(text: text));
    if (!mounted) return;
    ScaffoldMessenger.of(context).showSnackBar(
      const SnackBar(
        content: Text('Diagnostics copied'),
        duration: Duration(seconds: 2),
      ),
    );
  }

  Widget _code(String text) => Padding(
        padding: const EdgeInsets.only(top: 4),
        child: Text(
          text,
          overflow: TextOverflow.ellipsis,
          style: const TextStyle(
              fontFamily: 'monospace', fontSize: 12, color: Color(0xFF6366F1)),
        ),
      );
}

/// Renders either a warning ("you're sending traffic in cleartext to a remote
/// host — consider HTTPS") or a small "Secure connection" badge, based on the
/// scheme and whether the host is on the local network. Renders nothing when
/// the URL is incomplete or invalid so we don't nag while the user is typing.
class _SecurityHint extends StatelessWidget {
  final String rawUrl;
  const _SecurityHint({required this.rawUrl});

  @override
  Widget build(BuildContext context) {
    final ServerUrl url;
    try {
      url = ServerUrl.parse(rawUrl);
    } on FormatException {
      return const SizedBox.shrink();
    }

    final scheme = Theme.of(context).colorScheme;

    if (url.isInsecureRemote) {
      return Padding(
        padding: const EdgeInsets.only(top: 8),
        child: Row(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Icon(Icons.lock_open_outlined,
                size: 14, color: scheme.tertiary),
            const SizedBox(width: 6),
            Expanded(
              child: Text(
                'You are using an insecure connection. '
                'Consider using HTTPS for remote access.',
                style: TextStyle(fontSize: 12, color: scheme.tertiary),
              ),
            ),
          ],
        ),
      );
    }

    if (url.isSecure) {
      return Padding(
        padding: const EdgeInsets.only(top: 8),
        child: Row(
          children: [
            Icon(Icons.lock_outline, size: 14, color: Colors.green.shade700),
            const SizedBox(width: 6),
            Text(
              'Secure connection',
              style:
                  TextStyle(fontSize: 12, color: Colors.green.shade700),
            ),
          ],
        ),
      );
    }

    return const SizedBox.shrink();
  }
}
