import 'okx_instrument.dart';

class OkxRankedInstrument {
  const OkxRankedInstrument({
    required this.instrument,
    required this.todayChangePercent,
    required this.lastPrice,
  });

  final OkxInstrument instrument;
  final double todayChangePercent;
  final double lastPrice;
}
