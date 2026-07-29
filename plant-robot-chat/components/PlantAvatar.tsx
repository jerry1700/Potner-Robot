/**
 * 초록이 아바타. 외부 이미지 요청 없이 렌더되도록 인라인 SVG로 그린다
 * (네트워크 없이도 빌드/실행 가능해야 함).
 *
 * 장식용이라 aria-hidden — 화면마다 여러 번 등장하므로 이름을 붙이면
 * 스크린리더가 "초록이"를 계속 되읽는다. 이름은 옆의 텍스트/sr-only가 담당한다.
 */
export function PlantAvatar({ size = 32 }: { size?: number }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 32 32"
      className="shrink-0"
      aria-hidden="true"
      focusable="false"
    >
      <circle cx="16" cy="16" r="16" className="fill-leaf-light" />

      {/* 화분 */}
      <path
        d="M9.2 21.6h13.6l-1.5 6.1a1.7 1.7 0 0 1-1.65 1.3h-7.3a1.7 1.7 0 0 1-1.65-1.3L9.2 21.6Z"
        className="fill-soil"
      />
      <rect x="8.2" y="19.6" width="15.6" height="2.8" rx="1.3" className="fill-soil-light" />

      {/* 줄기 */}
      <path
        d="M16 20.2c0-3.4.2-6 .6-8.4"
        className="stroke-leaf-deep"
        strokeWidth="1.7"
        strokeLinecap="round"
        fill="none"
      />

      {/* 잎 두 장 — 오른쪽이 앞장이라 진한 색 */}
      <path
        d="M0 0C2.9-4.2 8.6-4.2 11.5 0 8.6 4.2 2.9 4.2 0 0Z"
        transform="translate(16 15.4) rotate(212) scale(0.86)"
        className="fill-leaf"
      />
      <path
        d="M0 0C2.9-4.2 8.6-4.2 11.5 0 8.6 4.2 2.9 4.2 0 0Z"
        transform="translate(16.4 12.6) rotate(-38)"
        className="fill-leaf-deep"
      />
      <path
        d="M0 0h9"
        transform="translate(16.4 12.6) rotate(-38)"
        className="stroke-leaf-light"
        strokeWidth="0.9"
        strokeLinecap="round"
        fill="none"
      />
    </svg>
  );
}
