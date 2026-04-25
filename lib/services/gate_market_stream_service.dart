import 'dart:async';
import 'dart:convert';

import 'package:web_socket/web_socket.dart' as ws;

import '../models/candle_interval.dart';
import '../models/gate_ticker.dart';
import '../models/hourly_candle.dart';

typedef GateTickerCallback = void Function(GateTicker24h ticker);
typedef GateCandleCallback = void Function(
  String contract,
  CandleInterval interval,
  HourlyCandle candle,
);
typedef GateStreamErrorCallback = void Function(String message);

class GateMarketStreamService {
  GateMarketStreamService()
      : _client = _GateWsClient(
          uri: Uri.parse('wss://fx-ws.gateio.ws/v4/ws/usdt'),
        ) {
    _client.onJsonMessage = _handleMessage;
  }

  final _GateWsClient _client;

  GateTickerCallback? _onTicker;
  GateCandleCallback? _onCandle;
  GateStreamErrorCallback? _onError;

  Future<void> start({
    required GateTickerCallback onTicker,
    required GateCandleCallback onCandle,
    GateStreamErrorCallback? onError,
  }) async {
    _onTicker = onTicker;
    _onCandle = onCandle;
    _onError = onError;
    _client.onErrorMessage = _emitError;
    await _client.ensureConnected();
  }

  Future<void> updateTickerSubscriptions(Iterable<String> contracts) {
    final sorted = contracts.toSet().toList()..sort();
    final args = sorted.isEmpty
        ? const <_GateSubscription>[]
        : <_GateSubscription>[_GateSubscription.tickers(sorted)];
    return _client.replaceSubscriptions(args);
  }

  Future<void> updateCandleSubscriptions({
    required Iterable<String> contracts,
    required Iterable<CandleInterval> intervals,
  }) {
    final args = <_GateSubscription>[];
    for (final interval in intervals) {
      for (final contract in contracts) {
        args.add(_GateSubscription.candlestick(contract, interval));
      }
    }
    return _client.replaceSubscriptions(args);
  }

  void dispose() {
    _client.dispose();
  }

  void _handleMessage(Map<String, dynamic> message) {
    final channel = message['channel']?.toString() ?? '';
    final event = message['event']?.toString() ?? '';
    if (event == 'subscribe' || event == 'unsubscribe' || event == 'pong') {
      return;
    }
    if (channel == 'futures.pong') {
      return;
    }
    if (event == 'error') {
      _emitError(message['error']?.toString() ?? 'Gate stream error');
      return;
    }

    final result = message['result'];
    if (channel == 'futures.tickers' && event == 'update' && result is List<dynamic>) {
      for (final row in result.whereType<Map<String, dynamic>>()) {
        _onTicker?.call(GateTicker24h.fromJson(row));
      }
      return;
    }

    if (channel == 'futures.candlesticks' && event == 'update' && result is List<dynamic>) {
      for (final row in result.whereType<Map<String, dynamic>>()) {
        final name = row['n']?.toString() ?? '';
        final splitIndex = name.indexOf('_');
        if (splitIndex <= 0 || splitIndex >= name.length - 1) {
          continue;
        }
        final intervalValue = name.substring(0, splitIndex);
        final contract = name.substring(splitIndex + 1);
        final interval = CandleInterval.values.firstWhere(
          (item) => item.gateInterval == intervalValue,
          orElse: () => CandleInterval.h1,
        );
        try {
          _onCandle?.call(
            contract,
            interval,
            HourlyCandle(
              openTime: DateTime.fromMillisecondsSinceEpoch(
                (int.parse(row['t'].toString())) * 1000,
                isUtc: true,
              ),
              open: double.parse(row['o'].toString()),
              high: double.parse(row['h'].toString()),
              low: double.parse(row['l'].toString()),
              close: double.parse(row['c'].toString()),
              volume: double.tryParse(row['v'].toString()) ?? 0,
              isConfirmed: row['w'] == true,
            ),
          );
        } catch (_) {
          // Ignore malformed rows and keep the stream alive.
        }
      }
      return;
    }

    final type = message['type']?.toString();
    if (type != null && type.isNotEmpty) {
      _emitError(message['msg']?.toString() ?? 'Gate stream notice: $type');
    }
  }

  void _emitError(String message) {
    _onError?.call(message);
  }
}

class _GateSubscription {
  const _GateSubscription({
    required this.channel,
    required this.payload,
    required this.key,
  });

  factory _GateSubscription.tickers(List<String> contracts) {
    return _GateSubscription(
      channel: 'futures.tickers',
      payload: contracts,
      key: 'ticker:all',
    );
  }

  factory _GateSubscription.candlestick(String contract, CandleInterval interval) {
    return _GateSubscription(
      channel: 'futures.candlesticks',
      payload: <String>[interval.gateInterval, contract],
      key: 'candle:${interval.name}:$contract',
    );
  }

  final String channel;
  final List<String> payload;
  final String key;
}

class _GateWsClient {
  _GateWsClient({required this.uri});

  final Uri uri;

  final Map<String, _GateSubscription> _desiredSubscriptions =
      <String, _GateSubscription>{};

  ws.WebSocket? _socket;
  StreamSubscription<dynamic>? _subscription;
  Timer? _appPingTimer;
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

  Future<void> replaceSubscriptions(List<_GateSubscription> nextArgs) async {
    final previousMap = Map<String, _GateSubscription>.from(_desiredSubscriptions);
    final nextMap = <String, _GateSubscription>{
      for (final arg in nextArgs) arg.key: arg,
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

    for (final key in removedKeys) {
      await _sendSubscription('unsubscribe', previousMap[key]!);
    }
    for (final key in addedKeys) {
      await _sendSubscription('subscribe', nextMap[key]!);
    }
  }

  void dispose() {
    _isDisposed = true;
    _appPingTimer?.cancel();
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
      _resetAppHeartbeat();
      for (final subscription in _desiredSubscriptions.values) {
        await _sendSubscription('subscribe', subscription);
      }
    } catch (error) {
      onErrorMessage?.call('Gate WebSocket connect failed: $error');
      _scheduleReconnect();
    } finally {
      _isConnecting = false;
    }
  }

  Future<ws.WebSocket> _connectSocket(Uri target) {
    return ws.WebSocket.connect(target);
  }

  void _handleSocketEvent(ws.WebSocketEvent event) {
    switch (event) {
      case ws.TextDataReceived(text: final text):
        try {
          final decoded = jsonDecode(text);
          if (decoded is Map<String, dynamic>) {
            final channel = decoded['channel']?.toString();
            if (channel == 'futures.ping') {
              _pongTimeoutTimer?.cancel();
            }
            onJsonMessage?.call(decoded);
          }
        } catch (_) {
          // Ignore unknown frames.
        }
      case ws.BinaryDataReceived():
        return;
      case ws.CloseReceived(code: final code, reason: final reason):
        onErrorMessage?.call('Gate WebSocket closed: ${code ?? 1005} ${reason.trim()}');
        _scheduleReconnect();
    }
  }

  Future<void> _sendSubscription(String event, _GateSubscription subscription) async {
    if (_socket == null) {
      return;
    }
    _socket?.sendText(
      jsonEncode({
        'time': DateTime.now().millisecondsSinceEpoch ~/ 1000,
        'channel': subscription.channel,
        'event': event,
        'payload': subscription.payload,
      }),
    );
    await Future<void>.delayed(const Duration(milliseconds: 80));
  }

  void _resetAppHeartbeat() {
    _appPingTimer?.cancel();
    _pongTimeoutTimer?.cancel();
    _appPingTimer = Timer.periodic(const Duration(seconds: 20), (_) {
      if (_socket == null) {
        return;
      }
      _socket?.sendText(
        jsonEncode({
          'time': DateTime.now().millisecondsSinceEpoch ~/ 1000,
          'channel': 'futures.ping',
        }),
      );
      _pongTimeoutTimer?.cancel();
      _pongTimeoutTimer = Timer(const Duration(seconds: 10), () {
        _socket?.close();
      });
    });
  }

  void _scheduleReconnect() {
    _socket = null;
    _appPingTimer?.cancel();
    _pongTimeoutTimer?.cancel();
    if (_isDisposed || _reconnectTimer != null) {
      return;
    }
    _reconnectTimer = Timer(const Duration(seconds: 2), () {
      _reconnectTimer = null;
      unawaited(_connect());
    });
  }
}
