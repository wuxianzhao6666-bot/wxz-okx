import 'candle_interval.dart';
import 'scan_strategy.dart';

class ScanConfig {
  const ScanConfig({
    required this.breakoutCandleCount,
    required this.lookbackHours,
    required this.minHourlyGainPercent,
    required this.preBreakoutMinSequenceLength,
    required this.candleInterval,
    required this.strategy,
  });

  const ScanConfig.defaults()
    : breakoutCandleCount = 2,
      lookbackHours = 24,
      minHourlyGainPercent = 1,
      preBreakoutMinSequenceLength = 2,
      candleInterval = CandleInterval.h1,
      strategy = ScanStrategy.breakout;

  /// 突破段：末端连续满足条件的阳线 **最少** 根数（实际可取更长的一段）。
  final int breakoutCandleCount;
  final int lookbackHours;
  final double minHourlyGainPercent;
  final int preBreakoutMinSequenceLength;
  final CandleInterval candleInterval;
  final ScanStrategy strategy;

  double get minHourlyGainRatio => minHourlyGainPercent / 100;

  ScanConfig copyWith({
    int? breakoutCandleCount,
    int? lookbackHours,
    double? minHourlyGainPercent,
    int? preBreakoutMinSequenceLength,
    CandleInterval? candleInterval,
    ScanStrategy? strategy,
  }) {
    return ScanConfig(
      breakoutCandleCount: breakoutCandleCount ?? this.breakoutCandleCount,
      lookbackHours: lookbackHours ?? this.lookbackHours,
      minHourlyGainPercent: minHourlyGainPercent ?? this.minHourlyGainPercent,
      preBreakoutMinSequenceLength:
          preBreakoutMinSequenceLength ?? this.preBreakoutMinSequenceLength,
      candleInterval: candleInterval ?? this.candleInterval,
      strategy: strategy ?? this.strategy,
    );
  }
}
