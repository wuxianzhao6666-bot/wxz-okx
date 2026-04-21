import 'package:flutter_test/flutter_test.dart';

import 'package:aiokx/main.dart';

void main() {
  testWidgets('scanner page renders shell widgets', (
    WidgetTester tester,
  ) async {
    await tester.pumpWidget(const AiOkxApp());

    expect(find.text('OKX 合约扫描'), findsOneWidget);
    expect(find.text('参数设置'), findsOneWidget);
    expect(find.text('规则说明'), findsOneWidget);
  });
}
