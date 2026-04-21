import 'dart:convert';
import 'dart:io' show Platform;

import 'package:cupertino_http/cupertino_http.dart';
import 'package:crypto/crypto.dart';
import 'package:http/http.dart' as http;

import '../models/hourly_candle.dart';
import '../models/okx_endpoint_config.dart';
import '../models/okx_instrument.dart';
import '../models/okx_pending_order.dart';
import '../models/okx_position.dart';
import '../models/okx_ranked_instrument.dart';
import '../models/okx_ticker.dart';

const okxApiKey = String.fromEnvironment('OKX_API_KEY');
const okxApiSecret = String.fromEnvironment('OKX_API_SECRET');
const okxApiPassphrase = String.fromEnvironment('OKX_API_PASSPHRASE');
const okxSimulatedTrading = bool.fromEnvironment(
  'OKX_SIMULATED_TRADING',
  defaultValue: true,
);

class OkxApiService {
  OkxApiService({
    http.Client? client,
    OkxEndpointConfig endpoint = OkxEndpointConfig.global,
  }) : _client = client ?? _createClient(),
       _endpoint = endpoint;

  final http.Client _client;
  final OkxEndpointConfig _endpoint;

  bool get hasTradingCredentials =>
      okxApiKey.isNotEmpty &&
      okxApiSecret.isNotEmpty &&
      okxApiPassphrase.isNotEmpty;

  bool get canAutoTrade => hasTradingCredentials;

  static http.Client _createClient() {
    if (Platform.isIOS || Platform.isMacOS) {
      return CupertinoClient.defaultSessionConfiguration();
    }
    return http.Client();
  }

  Future<List<OkxInstrument>> fetchUsdtSwapInstruments() async {
    final uri = Uri.https(_host, '/api/v5/public/instruments', {
      'instType': 'SWAP',
    });
    final json = await _getJson(uri);
    final data = (json['data'] as List<dynamic>? ?? const <dynamic>[])
        .cast<Map<String, dynamic>>();

    return data
        .map(OkxInstrument.fromJson)
        .where(
          (instrument) =>
              instrument.isLive && instrument.settleCcy.toUpperCase() == 'USDT',
        )
        .toList()
      ..sort((a, b) => a.instId.compareTo(b.instId));
  }

  /// 全市场 SWAP 行情，用于展示最新价格。
  Future<Map<String, OkxTicker24h>> fetchSwapTickerMap() async {
    final uri = Uri.https(_host, '/api/v5/market/tickers', {
      'instType': 'SWAP',
    });
    final json = await _getJson(uri);
    final data = (json['data'] as List<dynamic>? ?? const <dynamic>[])
        .cast<Map<String, dynamic>>();

    final map = <String, OkxTicker24h>{};
    for (final row in data) {
      final t = OkxTicker24h.fromJson(row);
      if (t.instId.isEmpty) {
        continue;
      }
      map[t.instId] = t;
    }
    return map;
  }

  Future<Map<String, double>> fetchSwap24hChangePercentMap() async {
    final tickerMap = await fetchSwapTickerMap();
    return {
      for (final entry in tickerMap.entries)
        entry.key: entry.value.changePercent24h,
    };
  }

  Future<List<OkxRankedInstrument>> fetchTopUsdtSwapRankings({
    int limit = 30,
  }) async {
    final results = await Future.wait([
      fetchUsdtSwapInstruments(),
      fetchSwapTickerMap(),
    ]);

    final instruments = results[0] as List<OkxInstrument>;
    final tickerMap = results[1] as Map<String, OkxTicker24h>;

    final ranked = instruments
        .map((instrument) {
          final ticker = tickerMap[instrument.instId];
          final todayChangePercent =
              ticker?.todayChangePercent ?? double.negativeInfinity;
          return OkxRankedInstrument(
            instrument: instrument,
            todayChangePercent: todayChangePercent,
            lastPrice: ticker?.lastPrice ?? 0,
          );
        })
        .where((item) => item.todayChangePercent.isFinite)
        .toList()
      ..sort(
        (a, b) => b.todayChangePercent.compareTo(a.todayChangePercent),
      );

    if (ranked.length <= limit) {
      return ranked;
    }
    return ranked.take(limit).toList();
  }

  Future<List<HourlyCandle>> fetchCandles(
    String instId, {
    required String bar,
    int limit = 80,
  }) async {
    final uri = Uri.https(_host, '/api/v5/market/history-candles', {
      'instId': instId,
      'bar': bar,
      'limit': '$limit',
    });
    final json = await _getJson(uri);
    final data = (json['data'] as List<dynamic>? ?? const <dynamic>[]);

    final candles =
        data
            .whereType<List<dynamic>>()
            .map(HourlyCandle.fromOkxRow)
            .where((candle) => candle.isConfirmed)
            .toList()
          ..sort((a, b) => a.openTime.compareTo(b.openTime));

    return candles;
  }

  Future<Map<String, dynamic>> _getJson(Uri uri) async {
    final response = await _client
        .get(
          uri,
          headers: const {
            'Accept': 'application/json',
            'User-Agent': 'aiokx-scanner/1.0',
          },
        )
        .timeout(const Duration(seconds: 15));

    if (response.statusCode != 200) {
      throw OkxApiException(
        'OKX request failed: ${response.statusCode} ${response.reasonPhrase}',
      );
    }

    final body = jsonDecode(response.body) as Map<String, dynamic>;
    if (body['code'] != '0') {
      throw OkxApiException(body['msg'] as String? ?? 'OKX returned an error');
    }

    return body;
  }

  void dispose() {
    _client.close();
  }

  String get _host => _endpoint.restHost;

  static String buildOkxTimestamp() {
    final now = DateTime.now().toUtc();
    final month = now.month.toString().padLeft(2, '0');
    final day = now.day.toString().padLeft(2, '0');
    final hour = now.hour.toString().padLeft(2, '0');
    final minute = now.minute.toString().padLeft(2, '0');
    final second = now.second.toString().padLeft(2, '0');
    final millisecond = now.millisecond.toString().padLeft(3, '0');
    return '${now.year}-$month-${day}T$hour:$minute:$second.${millisecond}Z';
  }
}

class OkxTradeOrderRequest {
  const OkxTradeOrderRequest({
    required this.instId,
    required this.side,
    required this.posSide,
    required this.tdMode,
    required this.ordType,
    required this.sz,
    this.px,
    this.attachAlgoOrds = const <Map<String, dynamic>>[],
  });

  final String instId;
  final String side;
  final String posSide;
  final String tdMode;
  final String ordType;
  final String sz;
  final String? px;
  final List<Map<String, dynamic>> attachAlgoOrds;

  Map<String, dynamic> toJson() {
    return {
      'instId': instId,
      'side': side,
      'posSide': posSide,
      'tdMode': tdMode,
      'ordType': ordType,
      'sz': sz,
      if (px != null && px!.isNotEmpty) 'px': px,
      if (attachAlgoOrds.isNotEmpty) 'attachAlgoOrds': attachAlgoOrds,
    };
  }
}

extension OkxPrivateApi on OkxApiService {
  Future<List<OkxPendingOrder>> fetchPendingOrders() async {
    final json = await _getPrivateJson('/api/v5/trade/orders-pending', {});
    final data = (json['data'] as List<dynamic>? ?? const <dynamic>[])
        .whereType<Map<String, dynamic>>();
    return data
        .map(OkxPendingOrder.fromJson)
        .where((order) => order.instId.endsWith('-SWAP'))
        .toList()
      ..sort((a, b) {
        final aTime = a.createdAt?.millisecondsSinceEpoch ?? 0;
        final bTime = b.createdAt?.millisecondsSinceEpoch ?? 0;
        return bTime.compareTo(aTime);
      });
  }

  Future<List<OkxPosition>> fetchPositions() async {
    final json = await _getPrivateJson('/api/v5/account/positions', {});
    final data = (json['data'] as List<dynamic>? ?? const <dynamic>[])
        .whereType<Map<String, dynamic>>();
    return data
        .map(OkxPosition.fromJson)
        .where((position) => position.instId.endsWith('-SWAP'))
        .where((position) => position.positionSize != 0)
        .toList()
      ..sort((a, b) {
        final aTime = a.updatedAt?.millisecondsSinceEpoch ?? 0;
        final bTime = b.updatedAt?.millisecondsSinceEpoch ?? 0;
        return bTime.compareTo(aTime);
      });
  }

  Future<double> fetchMaxLeverage(String instId) async {
    final json = await _getPrivateJson(
      '/api/v5/account/adjust-leverage-info',
      {
        'instType': 'SWAP',
        'instId': instId,
        'mgnMode': 'cross',
        'lever': '1',
      },
    );
    final data = (json['data'] as List<dynamic>? ?? const <dynamic>[])
        .whereType<Map<String, dynamic>>()
        .toList();
    final first = data.isEmpty ? null : data.first;
    return double.tryParse(first?['maxLever']?.toString() ?? '') ?? 1;
  }

  Future<void> setLeverage({
    required String instId,
    required double leverage,
  }) async {
    await _postPrivateJson('/api/v5/account/set-leverage', {
      'instId': instId,
      'lever': leverage.toStringAsFixed(0),
      'mgnMode': 'cross',
    });
  }

  Future<void> setPositionMode() async {
    await _postPrivateJson('/api/v5/account/set-position-mode', {
      'posMode': 'long_short_mode',
    });
  }

  Future<Map<String, dynamic>> placeOrder(OkxTradeOrderRequest request) async {
    final decoded = await _postPrivateJson('/api/v5/trade/order', request.toJson());
    final data = (decoded['data'] as List<dynamic>? ?? const <dynamic>[])
        .whereType<Map<String, dynamic>>()
        .toList();
    if (data.isEmpty) {
      throw const OkxApiException('下单返回为空');
    }

    final first = data.first;
    final sCode = first['sCode']?.toString() ?? '';
    if (sCode.isNotEmpty && sCode != '0') {
      final sMsg = first['sMsg']?.toString() ?? '';
      final subCode = first['subCode']?.toString() ?? '';
      final detail = [
        if (sMsg.isNotEmpty) sMsg,
        if (subCode.isNotEmpty) 'subCode: $subCode',
        if (sCode.isNotEmpty) 'sCode: $sCode',
      ].join(' | ');
      throw OkxApiException(detail.isEmpty ? '下单失败' : detail);
    }
    return decoded;
  }

  Future<Map<String, dynamic>> _getPrivateJson(
    String path,
    Map<String, String> query,
  ) async {
    if (!hasTradingCredentials) {
      throw const OkxApiException('Trading credentials are not configured');
    }

    final uri = Uri.https(
      _host,
      path,
      query.isEmpty ? null : query,
    );
    final requestPath = uri.hasQuery ? '$path?${uri.query}' : path;
    final timestamp = OkxApiService.buildOkxTimestamp();
    final signPayload = '${timestamp}GET$requestPath';
    final hmac = Hmac(sha256, utf8.encode(okxApiSecret));
    final sign = base64.encode(hmac.convert(utf8.encode(signPayload)).bytes);

    final response = await _client
        .get(
          uri,
          headers: {
            'Accept': 'application/json',
            'Content-Type': 'application/json',
            'OK-ACCESS-KEY': okxApiKey,
            'OK-ACCESS-SIGN': sign,
            'OK-ACCESS-TIMESTAMP': timestamp,
            'OK-ACCESS-PASSPHRASE': okxApiPassphrase,
            if (okxSimulatedTrading) 'x-simulated-trading': '1',
          },
        )
        .timeout(const Duration(seconds: 20));

    if (response.statusCode != 200) {
      throw OkxApiException(
        'OKX private request failed: ${response.statusCode} ${response.reasonPhrase} ${response.body}',
      );
    }

    final decoded = jsonDecode(response.body) as Map<String, dynamic>;
    if (decoded['code'] != '0') {
      throw OkxApiException(
        '[$path] code=${decoded['code']} msg=${decoded['msg'] ?? ''} body=${response.body}',
      );
    }
    return decoded;
  }

  Future<Map<String, dynamic>> _postPrivateJson(
    String path,
    Map<String, dynamic> body,
  ) async {
    if (!hasTradingCredentials) {
      throw const OkxApiException('Trading credentials are not configured');
    }

    final timestamp = OkxApiService.buildOkxTimestamp();
    final bodyString = jsonEncode(body);
    final signPayload = '${timestamp}POST$path$bodyString';
    final hmac = Hmac(sha256, utf8.encode(okxApiSecret));
    final sign = base64.encode(hmac.convert(utf8.encode(signPayload)).bytes);

    final response = await _client
        .post(
          Uri.https(_host, path),
          headers: {
            'Accept': 'application/json',
            'Content-Type': 'application/json',
            'OK-ACCESS-KEY': okxApiKey,
            'OK-ACCESS-SIGN': sign,
            'OK-ACCESS-TIMESTAMP': timestamp,
            'OK-ACCESS-PASSPHRASE': okxApiPassphrase,
            if (okxSimulatedTrading) 'x-simulated-trading': '1',
          },
          body: bodyString,
        )
        .timeout(const Duration(seconds: 20));

    if (response.statusCode != 200) {
      throw OkxApiException(
        'OKX private request failed: ${response.statusCode} ${response.reasonPhrase} ${response.body}',
      );
    }

    final decoded = jsonDecode(response.body) as Map<String, dynamic>;
    if (decoded['code'] != '0') {
      throw OkxApiException(
        '[$path] code=${decoded['code']} msg=${decoded['msg'] ?? ''} body=${response.body}',
      );
    }
    return decoded;
  }
}

class OkxApiException implements Exception {
  const OkxApiException(this.message);

  final String message;

  @override
  String toString() => 'OkxApiException: $message';
}
