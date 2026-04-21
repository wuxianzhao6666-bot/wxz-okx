import 'dart:math';

import '../models/hourly_candle.dart';
import '../models/okx_instrument.dart';
import '../models/scan_config.dart';
import '../models/scan_result.dart';
import '../models/scan_strategy.dart';
import 'okx_api_service.dart';

class ContractScannerService {
  ContractScannerService(this._apiService);

  final OkxApiService _apiService;

  static const int _batchSize = 8;
  static const int _topMoversCount = 30;

  Future<ScanSnapshot> scanOnce({required ScanConfig config}) async {
    final startedAt = DateTime.now();
    final instruments = await _apiService.fetchUsdtSwapInstruments();
    final changeMap = await _apiService.fetchSwap24hChangePercentMap();

    final ranked = instruments.toList()
      ..sort((a, b) {
        final ca = changeMap[a.instId] ?? double.negativeInfinity;
        final cb = changeMap[b.instId] ?? double.negativeInfinity;
        return cb.compareTo(ca);
      });

    final toScan = ranked.take(_topMoversCount).toList();

    final matches = <ScanResult>[];
    var failedCount = 0;

    for (var i = 0; i < toScan.length; i += _batchSize) {
      final batch = toScan.skip(i).take(_batchSize).toList();
      final batchResults = await Future.wait(
        batch.map((instrument) => _scanInstrument(instrument, config)),
      );

      for (final batchResult in batchResults) {
        if (batchResult.hasError) {
          failedCount += 1;
          continue;
        }
        if (batchResult.result == null) {
          continue;
        }
        matches.add(batchResult.result!);
      }
    }

    _sortMatches(matches, config.strategy);

    return ScanSnapshot(
      matches: matches,
      scannedCount: toScan.length,
      failedCount: failedCount,
      startedAt: startedAt,
      finishedAt: DateTime.now(),
    );
  }

  void _sortMatches(List<ScanResult> matches, ScanStrategy strategy) {
    if (strategy == ScanStrategy.breakout) {
      matches.sort((a, b) => b.breakoutStrength.compareTo(a.breakoutStrength));
    } else {
      matches.sort(
        (a, b) => (b.firstYangGainPercent ?? 0).compareTo(
          a.firstYangGainPercent ?? 0,
        ),
      );
    }
  }

  int _candleFetchLimit(ScanConfig config) {
    if (config.strategy == ScanStrategy.amplitudeChain) {
      return 100;
    }
    return max(
      120,
      config.lookbackHours + config.breakoutCandleCount + 80,
    );
  }

  Future<_InstrumentScanOutcome> _scanInstrument(
    OkxInstrument instrument,
    ScanConfig config,
  ) async {
    try {
      final candles = await _apiService.fetchCandles(
        instrument.instId,
        bar: config.candleInterval.okxBar,
        limit: _candleFetchLimit(config),
      );

      final ScanResult? result;
      if (config.strategy == ScanStrategy.breakout) {
        result = _matchBreakoutPattern(
          instrument: instrument,
          candles: candles,
          config: config,
        );
      } else {
        result = _matchAmplitudeChainPattern(
          instrument: instrument,
          candles: candles,
          config: config,
        );
      }

      if (result == null) {
        return const _InstrumentScanOutcome.empty();
      }
      return _InstrumentScanOutcome.result(result);
    } catch (_) {
      return const _InstrumentScanOutcome.error();
    }
  }

  ScanResult? _matchBreakoutPattern({
    required OkxInstrument instrument,
    required List<HourlyCandle> candles,
    required ScanConfig config,
  }) {
    final minBreakout = config.breakoutCandleCount;
    if (candles.isEmpty || minBreakout < 1) {
      return null;
    }

    final lastIdx = candles.length - 1;
    var streakStart = lastIdx;
    while (streakStart >= 0 &&
        _isQualifiedBullish(candles[streakStart], config)) {
      streakStart--;
    }
    final streakLen = lastIdx - streakStart;
    if (streakLen < minBreakout) {
      return null;
    }

    final breakoutStart = streakStart + 1;
    final lookbackStart = breakoutStart - config.lookbackHours;
    if (lookbackStart < 0) {
      return null;
    }

    final breakoutCandles = candles.sublist(breakoutStart, candles.length);
    final lookbackCandles = candles.sublist(lookbackStart, breakoutStart);
    final previousLookbackHigh = lookbackCandles
        .map((candle) => candle.high)
        .reduce(max);
    final breakoutHigh = breakoutCandles
        .map((candle) => candle.high)
        .reduce(max);

    if (breakoutHigh <= previousLookbackHigh) {
      return null;
    }

    final priorMomentumSequence = _findStrongBullishSequence(
      lookbackCandles,
      config,
    );
    if (priorMomentumSequence == null) {
      return null;
    }

    final priorFirst = priorMomentumSequence.first;
    final priorLast = priorMomentumSequence.last;
    final priorFirstLow = priorFirst.low;
    final priorLastHigh = priorLast.high;
    final purchaseReferencePrice =
        (priorLastHigh - priorFirstLow) + priorLastHigh;

    return ScanResult(
      strategy: ScanStrategy.breakout,
      instrument: instrument,
      scanTime: DateTime.now(),
      barLabel: config.candleInterval.label,
      lookbackHours: config.lookbackHours,
      previousLookbackHigh: previousLookbackHigh,
      breakoutHigh: breakoutHigh,
      breakoutPercents: breakoutCandles
          .map((candle) => candle.changePercent)
          .toList(growable: false),
      preBreakoutSequenceLength: priorMomentumSequence.length,
      preBreakoutSequenceStart: priorMomentumSequence.first.openTime,
      preBreakoutSequenceEnd: priorMomentumSequence.last.openTime,
      priorSequenceFirstLow: priorFirstLow,
      priorSequenceLastHigh: priorLastHigh,
      purchaseReferencePrice: purchaseReferencePrice,
    );
  }

  ScanResult? _matchAmplitudeChainPattern({
    required OkxInstrument instrument,
    required List<HourlyCandle> candles,
    required ScanConfig config,
  }) {
    if (candles.length < 4) {
      return null;
    }

    final n = candles.length;
    var i = n - 1;
    while (i >= 0 && candles[i].isBullish) {
      i -= 1;
    }
    final streak = n - 1 - i;

    if (streak >= 4) {
      final window = candles.sublist(n - 4, n);
      final hit = _validateAmplitudeChain(window, config);
      if (hit != null) {
        return _amplitudeScanResult(instrument, config, hit);
      }
    }
    if (streak >= 3) {
      final window = candles.sublist(n - 3, n);
      final hit = _validateAmplitudeChain(window, config);
      if (hit != null) {
        return _amplitudeScanResult(instrument, config, hit);
      }
    }
    return null;
  }

  List<HourlyCandle>? _validateAmplitudeChain(
    List<HourlyCandle> chain,
    ScanConfig config,
  ) {
    if (chain.length != 3 && chain.length != 4) {
      return null;
    }
    if (chain.any((c) => !c.isBullish)) {
      return null;
    }

    final first = chain.first;
    if (first.changeRatio < config.minHourlyGainRatio) {
      return null;
    }

    if (chain.length < 2 || chain[1].volume <= first.volume) {
      return null;
    }

    final a1 = first.amplitudeRatio;
    var sumRest = 0.0;
    for (var j = 1; j < chain.length; j++) {
      sumRest += chain[j].amplitudeRatio;
    }

    if (!_subsequentAmplitudesExceedTenTimesFirst(a1, sumRest)) {
      return null;
    }

    return chain;
  }

  /// 后续阳线振幅之和严格大于第一根阳线振幅的 10 倍。
  bool _subsequentAmplitudesExceedTenTimesFirst(
    double firstAmpRatio,
    double sumRestRatios,
  ) {
    return sumRestRatios > 10 * firstAmpRatio;
  }

  ScanResult _amplitudeScanResult(
    OkxInstrument instrument,
    ScanConfig config,
    List<HourlyCandle> chain,
  ) {
    return ScanResult(
      strategy: ScanStrategy.amplitudeChain,
      instrument: instrument,
      scanTime: DateTime.now(),
      barLabel: config.candleInterval.label,
      yangChainLength: chain.length,
      chainAmplitudePercents: chain
          .map((c) => c.amplitudePercent)
          .toList(growable: false),
      chainVolumes: chain.map((c) => c.volume).toList(growable: false),
      firstYangGainPercent: chain.first.changePercent,
    );
  }

  List<HourlyCandle>? _findStrongBullishSequence(
    List<HourlyCandle> candles,
    ScanConfig config,
  ) {
    var bestStart = -1;
    var bestLength = 0;
    var currentStart = -1;
    var currentLength = 0;

    for (var i = 0; i < candles.length; i++) {
      if (_isQualifiedBullish(candles[i], config)) {
        currentStart = currentStart == -1 ? i : currentStart;
        currentLength += 1;

        if (currentLength >= config.preBreakoutMinSequenceLength &&
            currentLength > bestLength) {
          bestStart = currentStart;
          bestLength = currentLength;
        }
      } else {
        currentStart = -1;
        currentLength = 0;
      }
    }

    if (bestLength < config.preBreakoutMinSequenceLength || bestStart == -1) {
      return null;
    }

    return candles.sublist(bestStart, bestStart + bestLength);
  }

  bool _isQualifiedBullish(HourlyCandle candle, ScanConfig config) {
    return candle.isBullish && candle.changeRatio >= config.minHourlyGainRatio;
  }
}

class _InstrumentScanOutcome {
  const _InstrumentScanOutcome.result(ScanResult this.result)
    : hasError = false;

  const _InstrumentScanOutcome.empty() : result = null, hasError = false;

  const _InstrumentScanOutcome.error() : result = null, hasError = true;

  final ScanResult? result;
  final bool hasError;
}
