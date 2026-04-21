enum CandleInterval {
  m1('1m', '1 分钟'),
  m5('5m', '5 分钟'),
  m15('15m', '15 分钟'),
  h1('1H', '1 小时'),
  h4('4H', '4 小时'),
  d1('1D', '日线');

  const CandleInterval(this.okxBar, this.label);

  final String okxBar;
  final String label;

  String get wsChannel {
    switch (this) {
      case CandleInterval.m1:
        return 'candle1m';
      case CandleInterval.m5:
        return 'candle5m';
      case CandleInterval.m15:
        return 'candle15m';
      case CandleInterval.h1:
        return 'candle1H';
      case CandleInterval.h4:
        return 'candle4H';
      case CandleInterval.d1:
        return 'candle1D';
    }
  }

  static CandleInterval? fromWsChannel(String channel) {
    switch (channel) {
      case 'candle1m':
        return CandleInterval.m1;
      case 'candle5m':
        return CandleInterval.m5;
      case 'candle15m':
        return CandleInterval.m15;
      case 'candle1H':
        return CandleInterval.h1;
      case 'candle4H':
        return CandleInterval.h4;
      case 'candle1D':
        return CandleInterval.d1;
      default:
        return null;
    }
  }
}
