import '../models/okx_instrument.dart';
import '../services/okx_api_service.dart';

class OkxAutoTradeTarget {
  const OkxAutoTradeTarget({
    required this.multiplier,
    required this.entryPrice,
  });

  final int multiplier;
  final double entryPrice;
}

class OkxAutoTradeService {
  OkxAutoTradeService(this._apiService);

  final OkxApiService _apiService;

  final Map<String, double> _maxLeverageCache = <String, double>{};
  bool _positionModeConfigured = false;

  static const Map<int, double> _marginByMultiplier = <int, double>{
    9: 5,
    10: 20,
    11: 20,
    12: 10,
  };

  Future<void> placeShortLadder({
    required OkxInstrument instrument,
    required List<OkxAutoTradeTarget> targets,
  }) async {
    if (!_apiService.canAutoTrade) {
      return;
    }
    if (targets.isEmpty) {
      return;
    }

    if (!_positionModeConfigured) {
      try {
        await _apiService.setPositionMode();
      } catch (error) {
        throw OkxApiException('设置持仓模式失败: $error');
      }
      _positionModeConfigured = true;
    }

    late final double maxLeverage;
    try {
      maxLeverage = await _fetchMaxLeverage(instrument.instId);
    } catch (error) {
      throw OkxApiException('获取最大杠杆失败: $error');
    }
    try {
      await _apiService.setLeverage(
        instId: instrument.instId,
        leverage: maxLeverage,
      );
    } catch (error) {
      throw OkxApiException('设置杠杆失败: $error');
    }

    final takeProfitPrice = _computeTakeProfitPrice(targets);
    for (final target in targets) {
      final marginUsd = _marginByMultiplier[target.multiplier];
      if (marginUsd == null || marginUsd <= 0) {
        continue;
      }

      final size = _computeOrderSize(
        instrument: instrument,
        entryPrice: target.entryPrice,
        leverage: maxLeverage,
        marginUsd: marginUsd,
      );
      if (size == null) {
        continue;
      }

      final entryPrice = _roundPriceDown(target.entryPrice, instrument.tickSz);
      final stopLossPrice = _roundPriceUp(entryPrice * 1.08, instrument.tickSz);
      final takeProfit = _roundPriceDown(
        takeProfitPrice,
        instrument.tickSz,
      );

      try {
        await _apiService.placeOrder(
          OkxTradeOrderRequest(
            instId: instrument.instId,
            side: 'sell',
            posSide: 'short',
            tdMode: 'cross',
            ordType: 'limit',
            sz: _formatSize(size, instrument.lotSz),
            px: _formatPrice(entryPrice, instrument.tickSz),
            attachAlgoOrds: [
              {
                'tpTriggerPx': _formatPrice(takeProfit, instrument.tickSz),
                'tpOrdPx': '-1',
                'tpTriggerPxType': 'last',
                'slTriggerPx': _formatPrice(stopLossPrice, instrument.tickSz),
                'slOrdPx': '-1',
                'slTriggerPxType': 'last',
              },
            ],
          ),
        );
      } catch (error) {
        throw OkxApiException('提交限价单失败: $error');
      }
    }
  }

  Future<void> placeManualShortAtPrice({
    required OkxInstrument instrument,
    required double entryPrice,
    required double marginUsd,
  }) async {
    if (!_apiService.canAutoTrade || entryPrice <= 0 || marginUsd <= 0) {
      return;
    }

    if (!_positionModeConfigured) {
      try {
        await _apiService.setPositionMode();
      } catch (error) {
        throw OkxApiException('设置持仓模式失败: $error');
      }
      _positionModeConfigured = true;
    }

    late final double maxLeverage;
    try {
      maxLeverage = await _fetchMaxLeverage(instrument.instId);
    } catch (error) {
      throw OkxApiException('获取最大杠杆失败: $error');
    }
    try {
      await _apiService.setLeverage(
        instId: instrument.instId,
        leverage: maxLeverage,
      );
    } catch (error) {
      throw OkxApiException('设置杠杆失败: $error');
    }

    final size = _computeOrderSize(
      instrument: instrument,
      entryPrice: entryPrice,
      leverage: maxLeverage,
      marginUsd: marginUsd,
    );
    if (size == null) {
      return;
    }

    try {
      await _apiService.placeOrder(
        OkxTradeOrderRequest(
          instId: instrument.instId,
          side: 'sell',
          posSide: 'short',
          tdMode: 'cross',
          ordType: 'market',
          sz: _formatSize(size, instrument.lotSz),
        ),
      );
    } catch (error) {
      throw OkxApiException('提交市价单失败: $error');
    }
  }

  Future<double> _fetchMaxLeverage(String instId) async {
    final cached = _maxLeverageCache[instId];
    if (cached != null && cached > 0) {
      return cached;
    }
    final maxLeverage = await _apiService.fetchMaxLeverage(instId);
    _maxLeverageCache[instId] = maxLeverage;
    return maxLeverage;
  }

  double _computeTakeProfitPrice(List<OkxAutoTradeTarget> targets) {
    final p9 = targets
        .where((target) => target.multiplier == 9)
        .map((target) => target.entryPrice)
        .cast<double?>()
        .firstWhere((price) => price != null, orElse: () => null);
    final p10 = targets
        .where((target) => target.multiplier == 10)
        .map((target) => target.entryPrice)
        .cast<double?>()
        .firstWhere((price) => price != null, orElse: () => null);

    if (p9 != null && p10 != null) {
      final delta = p10 - p9;
      final basePrice = p10 - delta * 10;
      return basePrice + (p10 - basePrice) * 0.4;
    }

    final sorted = [...targets]..sort((a, b) => a.multiplier.compareTo(b.multiplier));
    final fallbackEntry = sorted.first.entryPrice;
    return fallbackEntry * 0.92;
  }

  double? _computeOrderSize({
    required OkxInstrument instrument,
    required double entryPrice,
    required double leverage,
    required double marginUsd,
  }) {
    if (entryPrice <= 0 || leverage <= 0) {
      return null;
    }

    final contractValue = instrument.ctVal > 0 ? instrument.ctVal : 1.0;
    final step = instrument.lotSz > 0 ? instrument.lotSz : 1.0;
    final rawSize = marginUsd * leverage / (entryPrice * contractValue);
    final steppedSize = _floorToStep(rawSize, step);
    final minSize = instrument.minSz > 0 ? instrument.minSz : step;
    final normalizedMinSize = _ceilToStep(minSize, step);
    if (steppedSize < normalizedMinSize) {
      return normalizedMinSize;
    }
    return steppedSize;
  }

  double _floorToStep(double value, double step) {
    if (step <= 0) {
      return value;
    }
    return ((value / step).floorToDouble() * step).toDouble();
  }

  double _ceilToStep(double value, double step) {
    if (step <= 0) {
      return value;
    }
    return ((value / step).ceilToDouble() * step).toDouble();
  }

  double _roundPriceDown(double price, double tickSize) {
    if (tickSize <= 0) {
      return price;
    }
    return (price / tickSize).floorToDouble() * tickSize;
  }

  double _roundPriceUp(double price, double tickSize) {
    if (tickSize <= 0) {
      return price;
    }
    return (price / tickSize).ceilToDouble() * tickSize;
  }

  String _formatPrice(double value, double tickSize) {
    final decimals = _decimalPlaces(tickSize);
    return value.toStringAsFixed(decimals);
  }

  String _formatSize(double value, double lotSize) {
    final decimals = _decimalPlaces(lotSize);
    return value.toStringAsFixed(decimals);
  }

  int _decimalPlaces(double step) {
    if (step <= 0) {
      return 0;
    }
    final text = step.toString();
    if (!text.contains('.')) {
      return 0;
    }
    return text.split('.').last.replaceFirst(RegExp(r'0+$'), '').length;
  }
}
