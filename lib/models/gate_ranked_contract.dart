import 'gate_contract.dart';

class GateRankedContract {
  const GateRankedContract({
    required this.contract,
    required this.changePercent24h,
    required this.lastPrice,
  });

  final GateContract contract;
  final double changePercent24h;
  final double lastPrice;
}
