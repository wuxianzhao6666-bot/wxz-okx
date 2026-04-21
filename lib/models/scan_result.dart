import 'okx_instrument.dart';
import 'scan_strategy.dart';

class ScanResult {
  const ScanResult({
    required this.strategy,
    required this.instrument,
    required this.scanTime,
    required this.barLabel,
    this.lookbackHours,
    this.previousLookbackHigh,
    this.breakoutHigh,
    this.breakoutPercents,
    this.preBreakoutSequenceLength,
    this.preBreakoutSequenceStart,
    this.preBreakoutSequenceEnd,
    this.priorSequenceFirstLow,
    this.priorSequenceLastHigh,
    this.purchaseReferencePrice,
    this.yangChainLength,
    this.chainAmplitudePercents,
    this.chainVolumes,
    this.firstYangGainPercent,
  });

  final ScanStrategy strategy;
  final OkxInstrument instrument;
  final DateTime scanTime;
  final String barLabel;

  final int? lookbackHours;
  final double? previousLookbackHigh;
  final double? breakoutHigh;
  final List<double>? breakoutPercents;
  final int? preBreakoutSequenceLength;
  final DateTime? preBreakoutSequenceStart;
  final DateTime? preBreakoutSequenceEnd;

  /// 前置连阳第一根阳线的最低价（用于购买参考价计算）。
  final double? priorSequenceFirstLow;

  /// 前置连阳最后一根阳线的最高价。
  final double? priorSequenceLastHigh;

  /// `(末根最高价 - 首根最低价) + 末根最高价`，即 2×末高 - 首低。
  final double? purchaseReferencePrice;

  final int? yangChainLength;
  final List<double>? chainAmplitudePercents;
  final List<double>? chainVolumes;
  final double? firstYangGainPercent;

  double get breakoutStrength {
    if (strategy == ScanStrategy.breakout) {
      final high = previousLookbackHigh;
      if (high == null || high == 0) {
        return 0;
      }
      return (breakoutHigh ?? 0) / high - 1;
    }
    return (firstYangGainPercent ?? 0) / 100;
  }
}

class ScanSnapshot {
  const ScanSnapshot({
    required this.matches,
    required this.scannedCount,
    required this.failedCount,
    required this.startedAt,
    required this.finishedAt,
  });

  final List<ScanResult> matches;
  final int scannedCount;
  final int failedCount;
  final DateTime startedAt;
  final DateTime finishedAt;

  Duration get elapsed => finishedAt.difference(startedAt);
}
