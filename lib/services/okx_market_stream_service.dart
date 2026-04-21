import 'dart:async';
import 'dart:convert';
import 'dart:io';

import 'package:cupertino_http/cupertino_http.dart';
import 'package:web_socket/web_socket.dart' as ws;

import '../models/candle_interval.dart';
import '../models/hourly_candle.dart';
import '../models/okx_endpoint_config.dart';
import '../models/okx_ticker.dart';

typedef OkxTickerCallback = void Function(OkxTicker24h ticker);
typedef OkxCandleCallback = void Function(
  String instId,
  CandleInterval interval,
  HourlyCandle candle,
);
typedef OkxStreamErrorCallback = void Function(String message);

class OkxMarketStreamService {
  OkxMarketStreamService({
    OkxEndpointConfig endpoint = OkxEndpointConfig.global,
  }) : _publicClient = _OkxWsClient(
          uri: Uri.parse('wss://${endpoint.publicWsHost}:8443/ws/v5/public'),
        ),
        _businessClient = _OkxWsClient(
          uri: Uri.parse('wss://${endpoint.businessWsHost}:8443/ws/v5/business'),
        ) {
    _publicClient.onJsonMessage = _handlePublicMessage;
    _businessClient.onJsonMessage = _handleBusinessMessage;
  }

  final _OkxWsClient _publicClient;
  final _OkxWsClient _businessClient;

  OkxTickerCallback? _onTicker;
  OkxCandleCallback? _onCandle;
  OkxStreamErrorCallback? _onError;

  Future<void> start({
    required OkxTickerCallback onTicker,
    required OkxCandleCallback onCandle,
    OkxStreamErrorCallback? onError,
  }) async {
    _onTicker = onTicker;
    _onCandle = onCandle;
    _onError = onError;
    _publicClient.onErrorMessage = _emitError;
    _businessClient.onErrorMessage = _emitError;
    await Future.wait([
      _publicClient.ensureConnected(),
      _businessClient.ensureConnected(),
    ]);
  }

  Future<void> updateTickerSubscriptions(Iterable<String> instIds) {
    final args = instIds
        .map((instId) => {'channel': 'tickers', 'instId': instId})
        .toList();
    return _publicClient.replaceSubscriptions(args);
  }

  Future<void> updateCandleSubscriptions({
    required Iterable<String> instIds,
    required Iterable<CandleInterval> intervals,
  }) {
    final args = <Map<String, String>>[];
    for (final interval in intervals) {
      for (final instId in instIds) {
        args.add({'channel': interval.wsChannel, 'instId': instId});
      }
    }
    return _businessClient.replaceSubscriptions(args);
  }

  void dispose() {
    _publicClient.dispose();
    _businessClient.dispose();
  }

  void _handlePublicMessage(Map<String, dynamic> message) {
    final event = message['event']?.toString();
    if (event != null) {
      if (event == 'error' || event == 'notice') {
        _emitError(message['msg']?.toString() ?? 'OKX public stream error');
      }
      return;
    }

    final arg = message['arg'];
    final data = message['data'];
    if (arg is! Map<String, dynamic> || data is! List<dynamic>) {
      return;
    }
    if (arg['channel']?.toString() != 'tickers') {
      return;
    }

    for (final row in data.whereType<Map<String, dynamic>>()) {
      _onTicker?.call(OkxTicker24h.fromJson(row));
    }
  }

  void _handleBusinessMessage(Map<String, dynamic> message) {
    final event = message['event']?.toString();
    if (event != null) {
      if (event == 'error' || event == 'notice') {
        _emitError(message['msg']?.toString() ?? 'OKX business stream error');
      }
      return;
    }

    final arg = message['arg'];
    final data = message['data'];
    if (arg is! Map<String, dynamic> || data is! List<dynamic>) {
      return;
    }

    final instId = arg['instId']?.toString();
    final interval = CandleInterval.fromWsChannel(
      arg['channel']?.toString() ?? '',
    );
    if (instId == null || interval == null) {
      return;
    }

    for (final row in data.whereType<List<dynamic>>()) {
      try {
        _onCandle?.call(instId, interval, HourlyCandle.fromOkxRow(row));
      } catch (_) {
        // Ignore malformed rows and keep the stream alive.
      }
    }
  }

  void _emitError(String message) {
    _onError?.call(message);
  }
}

class _OkxWsClient {
  _OkxWsClient({required this.uri});

  final Uri uri;

  final Map<String, Map<String, String>> _desiredSubscriptions =
      <String, Map<String, String>>{};

  ws.WebSocket? _socket;
  StreamSubscription<dynamic>? _subscription;
  Timer? _pingTimer;
  Timer? _pongTimeoutTimer;
  Timer? _reconnectTimer;
  bool _isDisposed = false;
  bool _isConnecting = false;

  void Function(Map<String, dynamic> message)? onJsonMessage;
  void Function(String message)? onErrorMessage;

  Future<void> ensureConnected() async {
    if (_isDisposed || _socket != null || _isConnecting) {
      return;
    }
    await _connect();
  }

  Future<void> replaceSubscriptions(List<Map<String, String>> nextArgs) async {
    final previousMap = Map<String, Map<String, String>>.from(
      _desiredSubscriptions,
    );
    final nextMap = <String, Map<String, String>>{
      for (final arg in nextArgs) _subscriptionKey(arg): arg,
    };
    final currentKeys = previousMap.keys.toSet();
    final nextKeys = nextMap.keys.toSet();
    final removedKeys = currentKeys.difference(nextKeys);
    final addedKeys = nextKeys.difference(currentKeys);

    _desiredSubscriptions
      ..clear()
      ..addAll(nextMap);

    await ensureConnected();
    if (_socket == null) {
      return;
    }

    if (removedKeys.isNotEmpty) {
      await _sendOperation(
        'unsubscribe',
        removedKeys.map((key) => previousMap[key]!).toList(),
      );
    }
    if (addedKeys.isNotEmpty) {
      await _sendOperation(
        'subscribe',
        addedKeys.map((key) => nextMap[key]!).toList(),
      );
    }
  }

  void dispose() {
    _isDisposed = true;
    _pingTimer?.cancel();
    _pongTimeoutTimer?.cancel();
    _reconnectTimer?.cancel();
    unawaited(_subscription?.cancel());
    unawaited(_socket?.close());
  }

  Future<void> _connect() async {
    if (_isDisposed || _isConnecting) {
      return;
    }

    _isConnecting = true;
    try {
      final socket = await _connectSocket(uri);
      _socket = socket;
      _subscription = socket.events.listen(
        _handleSocketEvent,
        onError: (_) => _scheduleReconnect(),
        onDone: _scheduleReconnect,
      );
      _resetHeartbeat();
      if (_desiredSubscriptions.isNotEmpty) {
        await _sendOperation(
          'subscribe',
          _desiredSubscriptions.values.toList(),
        );
      }
    } catch (error) {
      onErrorMessage?.call('WebSocket connect failed: $error');
      _scheduleReconnect();
    } finally {
      _isConnecting = false;
    }
  }

  Future<ws.WebSocket> _connectSocket(Uri target) {
    if (Platform.isIOS || Platform.isMacOS) {
      return CupertinoWebSocket.connect(target);
    }
    return ws.WebSocket.connect(target);
  }

  void _handleSocketEvent(ws.WebSocketEvent event) {
    _resetHeartbeat();

    switch (event) {
      case ws.TextDataReceived(text: final text):
        if (text == 'pong') {
          _pongTimeoutTimer?.cancel();
          return;
        }
        try {
          final decoded = jsonDecode(text);
          if (decoded is Map<String, dynamic>) {
            onJsonMessage?.call(decoded);
          }
        } catch (_) {
          // Ignore unknown frames.
        }
      case ws.BinaryDataReceived():
        return;
      case ws.CloseReceived(code: final code, reason: final reason):
        onErrorMessage?.call('WebSocket closed: ${code ?? 1005} ${reason.trim()}');
        _scheduleReconnect();
    }
  }

  Future<void> _sendOperation(
    String op,
    List<Map<String, String>> args,
  ) async {
    if (_socket == null || args.isEmpty) {
      return;
    }

    const chunkSize = 80;
    for (var start = 0; start < args.length; start += chunkSize) {
      final end = (start + chunkSize < args.length)
          ? start + chunkSize
          : args.length;
      _socket?.sendText(jsonEncode({'op': op, 'args': args.sublist(start, end)}));
      if (end < args.length) {
        await Future<void>.delayed(const Duration(milliseconds: 350));
      }
    }
  }

  void _resetHeartbeat() {
    _pingTimer?.cancel();
    _pongTimeoutTimer?.cancel();
    _pingTimer = Timer(const Duration(seconds: 20), () {
      if (_socket == null) {
        return;
      }
      _socket?.sendText('ping');
      _pongTimeoutTimer = Timer(const Duration(seconds: 10), () {
        _socket?.close();
      });
    });
  }

  void _scheduleReconnect() {
    _socket = null;
    _pingTimer?.cancel();
    _pongTimeoutTimer?.cancel();
    if (_isDisposed || _reconnectTimer != null) {
      return;
    }
    _reconnectTimer = Timer(const Duration(seconds: 2), () {
      _reconnectTimer = null;
      unawaited(_connect());
    });
  }

  String _subscriptionKey(Map<String, String> arg) {
    final channel = arg['channel'] ?? '';
    final instId = arg['instId'] ?? '';
    return '$channel:$instId';
  }
}
