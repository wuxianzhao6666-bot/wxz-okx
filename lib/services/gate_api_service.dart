import 'dart:convert';

import 'package:http/http.dart' as http;

import '../models/candle_interval.dart';
import '../models/gate_contract.dart';
import '../models/gate_ticker.dart';
import '../models/hourly_candle.dart';

class GateApiService {
  GateApiService({http.Client? client}) : _client = client ?? _createClient();

  final http.Client _client;

  static http.Client _createClient() {
    // Gate requests can stall behind the Apple networking stack on some
    // proxy/VPN setups, so prefer the default Dart IO client here.
    return http.Client();
  }

  Future<List<GateContract>> fetchUsdtContracts() async {
    final uri = Uri.https('api.gateio.ws', '/api/v4/futures/usdt/contracts');
    final data = await _getJsonList(uri);
    return data
        .map(GateContract.fromJson)
        .where((contract) => contract.isTrading && contract.name.endsWith('_USDT'))
        .toList()
      ..sort((a, b) => a.name.compareTo(b.name));
  }

  Future<Map<String, GateTicker24h>> fetchTickerMap() async {
    final uri = Uri.https('api.gateio.ws', '/api/v4/futures/usdt/tickers');
    final data = await _getJsonList(uri);
    final map = <String, GateTicker24h>{};
    for (final row in data) {
      final ticker = GateTicker24h.fromJson(row);
      if (ticker.contract.isEmpty) {
        continue;
      }
      map[ticker.contract] = ticker;
    }
    return map;
  }

  Future<List<HourlyCandle>> fetchCandles(
    String contract, {
    required CandleInterval interval,
    int limit = 20,
  }) async {
    final uri = Uri.https('api.gateio.ws', '/api/v4/futures/usdt/candlesticks', {
      'contract': contract,
      'interval': interval.gateInterval,
      'limit': '$limit',
    });
    final data = await _getJsonList(uri);

    final candles = data
        .map((row) => HourlyCandle.fromGateJson(row, interval: interval.duration))
        .toList()
      ..sort((a, b) => a.openTime.compareTo(b.openTime));
    return candles;
  }

  Future<List<Map<String, dynamic>>> _getJsonList(Uri uri) async {
    final response = await _client
        .get(
          uri,
          headers: const {
            'Accept': 'application/json',
            'User-Agent': 'aiokx-scanner/1.0',
          },
        )
        .timeout(const Duration(seconds: 30));

    if (response.statusCode != 200) {
      throw GateApiException(
        'Gate request failed: ${response.statusCode} ${response.reasonPhrase}',
      );
    }

    final decoded = jsonDecode(response.body);
    if (decoded is! List<dynamic>) {
      throw const GateApiException('Gate returned an unexpected payload');
    }

    return decoded.whereType<Map<String, dynamic>>().toList();
  }

  void dispose() {
    _client.close();
  }
}

class GateApiException implements Exception {
  const GateApiException(this.message);

  final String message;

  @override
  String toString() => 'GateApiException: $message';
}
