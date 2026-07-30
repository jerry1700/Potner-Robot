import type { Metadata, Viewport } from 'next';
import type { ReactNode } from 'react';

import './globals.css';

/**
 * next/font 는 쓰지 않는다 — 빌드 중 폰트를 네트워크로 내려받으므로 오프라인에서 빌드가 깨진다.
 * 폰트 스택은 globals.css 의 --font-sans (시스템에 이미 있는 한글 폰트)로만 지정한다.
 */
export const metadata: Metadata = {
  title: '초록이 — 반려식물로봇',
  description:
    '반려식물로봇 초록이와 대화하며 물주기, 햇빛, 병충해, 계절 관리까지 함께 고민해요. 우리 집 식물 이야기를 편하게 들려주세요.',
  applicationName: '초록이',
  keywords: ['초록이', '반려식물', '식물 키우기', '식물 관리', '물주기', '병충해'],
};

export const viewport: Viewport = {
  width: 'device-width',
  initialScale: 1,
  // 모바일 브라우저 주소창 색을 캔버스 색과 맞춘다 (라이트/다크 각각)
  themeColor: [
    { media: '(prefers-color-scheme: light)', color: '#FDFBF7' },
    { media: '(prefers-color-scheme: dark)', color: '#1A1E1A' },
  ],
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="ko">
      {/* 채팅 레이아웃이 뷰포트를 꽉 채우려면 body까지 높이가 이어져야 한다 */}
      <body className="h-full bg-canvas font-sans text-ink antialiased">{children}</body>
    </html>
  );
}
