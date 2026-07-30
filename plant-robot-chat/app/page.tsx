import { ChatWindow } from '@/components/ChatWindow';

/**
 * 상태는 전부 ChatWindow(클라이언트) 안에 있으므로 이 페이지는 서버 컴포넌트로 둔다.
 */
export default function Page() {
  return <ChatWindow />;
}
