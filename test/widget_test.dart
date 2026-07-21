import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:potner_app/main.dart';

void main() {
  testWidgets('Potner app renders the initial screen', (
    WidgetTester tester,
  ) async {
    await tester.pumpWidget(const PotnerApp());

    expect(find.byType(MaterialApp), findsOneWidget);
    expect(find.text('Potner'), findsOneWidget);
    expect(find.text('Flutter project is ready.'), findsOneWidget);
  });
}
