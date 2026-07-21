import 'package:flutter/material.dart';

void main() {
  runApp(const PotnerApp());
}

class PotnerApp extends StatelessWidget {
  const PotnerApp({super.key});

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      debugShowCheckedModeBanner: false,
      title: 'Potner',
      home: Scaffold(
        appBar: AppBar(title: const Text('Potner')),
        body: const Center(child: Text('Flutter project is ready.')),
      ),
    );
  }
}
