"""speaker — 로봇이 말하는 유일한 노드.

로봇에는 마이크가 없습니다. 사용자는 앱에 말하고, 앱이 음성을 문자로
바꿔 서버에 보내고, 서버 LLM 이 만든 답을 MQTT 로 내려보냅니다. 로봇은
받은 문장을 소리로만 내보냅니다. 그래서 STT 없이 TTS 만 있습니다.

    앱(음성 입력) -> 서버(LLM) -> ??? -> tts/say -> speaker

★ 서버가 만든 대사를 로봇까지 전달하는 경로가 아직 없습니다.
  docs/DEVICE-MQTT.md 4절의 서버->장치 토픽은 command/expression 하나뿐이고,
  브로커 ACL 이 command/# 아래만 읽기 허용합니다. 서버 팀에 command/speech
  추가를 요청해 둔 상태입니다. 지금 tts/say 로 들어오는 것은 mission_manager
  의 인사말뿐입니다.

재생은 별도 스레드에서 순서대로 처리합니다. 콜백에서 바로 재생하면 실행기
스레드가 몇 초씩 막혀 다른 노드의 콜백까지 밀립니다. 문장이 겹쳐 들어와도
동시에 두 개가 울리지 않게 큐로 직렬화합니다.

하드웨어 경로는 USB 사운드카드 -> PAM8403 앰프 -> 8Ω 스피커입니다.
"""

import queue
import subprocess
import threading
from pathlib import Path

import rclpy
from rclpy.node import Node
from std_msgs.msg import String

from potner_base.speech import DEFAULT_COMMAND, build_command, normalize_text, wav_filename

# 큐가 이보다 길어지면 오래된 문장을 버립니다. 서버가 폭주해도 로봇이
# 몇 분 전 이야기를 계속 읊지 않게 하려는 것입니다.
MAX_QUEUE = 5


class Speaker(Node):
    def __init__(self):
        super().__init__("speaker")

        self.declare_parameter("tts_command", list(DEFAULT_COMMAND))
        self.declare_parameter("wav_dir", "")
        self.declare_parameter("wav_player", ["aplay", "-q", "{text}"])
        self.declare_parameter("max_text_length", 300)
        self.declare_parameter("enabled", True)

        self._tts_command = self.get_parameter("tts_command").value
        self._wav_dir = self.get_parameter("wav_dir").value
        self._wav_player = self.get_parameter("wav_player").value
        self._max_length = self.get_parameter("max_text_length").value
        self._enabled = self.get_parameter("enabled").value

        self._queue = queue.Queue()
        self._stop = threading.Event()
        self._worker = threading.Thread(
            target=self._playback_loop, name="speaker", daemon=True
        )
        self._worker.start()

        self.create_subscription(String, "tts/say", self._on_say, 10)
        self.get_logger().info(
            f"speaker 시작 (합성 명령: {' '.join(self._tts_command)})"
        )

    # --- 콜백 ---

    def _on_say(self, msg: String):
        if not self._enabled:
            return

        text = normalize_text(msg.data, self._max_length)
        if not text:
            return

        if self._queue.qsize() >= MAX_QUEUE:
            dropped = self._queue.get_nowait()
            self.get_logger().warn(f"큐가 밀려서 버립니다: {dropped[:20]}...")

        self._queue.put(text)

    # --- 재생 ---

    def _playback_loop(self):
        while not self._stop.is_set():
            try:
                text = self._queue.get(timeout=0.2)
            except queue.Empty:
                continue
            self._speak(text)

    def _speak(self, text: str):
        argv = self._resolve_command(text)
        if argv is None:
            return

        try:
            # shell=True 를 쓰지 않습니다. 문장은 서버 LLM 이 만든 외부
            # 문자열이라, 셸에 넘기면 임의 명령이 실행될 수 있습니다.
            subprocess.run(argv, check=True, timeout=60)
        except FileNotFoundError:
            self.get_logger().error(
                f"{argv[0]} 를 찾을 수 없습니다. "
                "sudo apt install espeak-ng 또는 tts_command 파라미터를 고치세요."
            )
        except subprocess.TimeoutExpired:
            self.get_logger().warn("음성 재생이 60초를 넘겨 중단했습니다.")
        except subprocess.CalledProcessError as exc:
            self.get_logger().error(f"음성 재생 실패 (종료코드 {exc.returncode})")

    def _resolve_command(self, text: str):
        """미리 녹음한 파일이 있으면 재생, 없으면 합성 명령을 만듭니다."""
        wav = self._find_wav(text)
        template = self._wav_player if wav else self._tts_command
        payload = str(wav) if wav else text

        try:
            return build_command(template, payload)
        except ValueError as exc:
            self.get_logger().error(f"명령 조립 실패: {exc}")
            return None

    def _find_wav(self, text: str):
        if not self._wav_dir:
            return None
        candidate = Path(self._wav_dir) / wav_filename(text)
        return candidate if candidate.is_file() else None

    def destroy_node(self):
        self._stop.set()
        self._worker.join(timeout=1.0)
        super().destroy_node()


def main(args=None):
    rclpy.init(args=args)
    node = Speaker()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == "__main__":
    main()
