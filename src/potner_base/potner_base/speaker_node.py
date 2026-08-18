"""speaker — 로봇이 말하는 유일한 노드.

로봇에는 마이크가 없습니다. 사용자는 휴대폰에 말하고, 로봇에서 돌아가는
음성 대화 서버(voice-chat-server/)가 STT -> LLM -> TTS 를 거쳐 오디오를
만들어 이 노드로 보냅니다. 로봇은 받은 것을 소리로만 내보냅니다.

    폰 브라우저 --(Wi-Fi)--> voice-chat-server (로봇 온보드)
                               | GMS STT -> LLM -> GMS TTS(wav)
                               | 조각을 스풀에 쓰고 경로를 발행
                               v
                            tts/play_audio -> speaker

들어오는 길이 둘입니다.

    tts/say         문장(String).  mission_manager 의 귀가 인사말.
                    espeak-ng 로 즉석 합성합니다. 품질보다 오프라인
                    동작이 중요한 자리입니다.
    tts/play_audio  봉투(String, JSON). 음성 대화 오디오 조각의 경로.
                    GMS 로 합성한 것이라 사람 목소리에 가깝습니다.

서버 MQTT 를 경유하지 않습니다. 브로커 ACL 이 장치에 command/# 읽기만
허용해서 서버->장치 대사 토픽을 쓸 수 없었고(docs/MQTT_CONTRACT.md),
음성 서버를 로봇에서 직접 띄우는 쪽으로 우회했습니다. 덕분에 서버 팀
작업을 기다리지 않고 닫힙니다.

재생은 별도 스레드에서 순서대로 처리합니다. 콜백에서 바로 재생하면 실행기
스레드가 몇 초씩 막혀 다른 노드의 콜백까지 밀립니다. 문장이 겹쳐 들어와도
동시에 두 개가 울리지 않게 큐로 직렬화합니다.

사용자가 새로 질문하면 이전 답변은 즉시 그쳐야 합니다. 그래서 재생기를
subprocess.run 이 아니라 Popen 으로 띄우고, 큐가 선점을 알리면 재생 중인
조각을 종료합니다. **인사말은 선점하지 않습니다** — 귀가 인사가 대화 때문에
잘리면 이상하고 mission_manager 가 다시 보내주지도 않습니다.

하드웨어 경로는 USB 사운드카드 -> PAM8403 앰프 -> 8Ω 스피커입니다.
"""

import dataclasses
import subprocess
import threading
import time
from pathlib import Path

import rclpy
from rclpy.node import Node
from std_msgs.msg import String

from potner_base.audio_queue import (
    MAX_PENDING,
    PlaybackDecision,
    PlaybackQueue,
    parse_play_request,
)
from potner_base.speech import (
    DEFAULT_AUDIO_COMMAND,
    DEFAULT_COMMAND,
    build_command,
    normalize_text,
    resolve_spool_path,
    wav_filename,
)

# 대기 상한은 audio_queue.MAX_PENDING 이 단일 출처입니다. 큐가 이보다
# 길어지면 오래된 문장(또는 새 조각)을 버립니다 — 서버가 폭주해도 로봇이
# 몇 분 전 이야기를 계속 읊지 않게 하려는 것입니다.

# 재생 중 선점 여부를 확인하는 주기. 짧을수록 끊김이 빠르지만 폴링이
# 잦아집니다. 사람이 "끊겼다"고 느끼는 한계가 대략 이 정도입니다.
CANCEL_POLL_SEC = 0.05

# 한 조각이 이보다 오래 걸리면 재생기가 멈춘 것으로 봅니다.
PLAYBACK_TIMEOUT_SEC = 60


class Speaker(Node):
    def __init__(self):
        super().__init__("speaker")

        self.declare_parameter("tts_command", list(DEFAULT_COMMAND))
        self.declare_parameter("wav_dir", "")
        self.declare_parameter("wav_player", ["aplay", "-q", "{text}"])
        self.declare_parameter("max_text_length", 300)
        self.declare_parameter("enabled", True)
        self.declare_parameter("audio_player", list(DEFAULT_AUDIO_COMMAND))
        self.declare_parameter("audio_spool_dir", "/tmp/potner-tts")
        self.declare_parameter("audio_queue_max", MAX_PENDING)

        self._tts_command = self.get_parameter("tts_command").value
        self._wav_dir = self.get_parameter("wav_dir").value
        self._wav_player = self.get_parameter("wav_player").value
        self._max_length = self.get_parameter("max_text_length").value
        self._enabled = self.get_parameter("enabled").value
        self._audio_player = self.get_parameter("audio_player").value
        self._audio_spool_dir = self.get_parameter("audio_spool_dir").value
        self._audio_queue_max = self.get_parameter("audio_queue_max").value

        self._queue = PlaybackQueue(max_pending=self._audio_queue_max)
        self._stop = threading.Event()
        self._worker = threading.Thread(
            target=self._playback_loop, name="speaker", daemon=True
        )
        self._worker.start()

        self.create_subscription(String, "tts/say", self._on_say, 10)
        self.create_subscription(String, "tts/play_audio", self._on_play_audio, 10)
        self.create_subscription(String, "tts/cancel", self._on_cancel, 10)
        self.get_logger().info(
            f"speaker 시작 (합성: {' '.join(self._tts_command)} / "
            f"재생: {' '.join(self._audio_player)} / 스풀: {self._audio_spool_dir})"
        )

    # --- 콜백 ---

    def _on_say(self, msg: String):
        if not self._enabled:
            return

        text = normalize_text(msg.data, self._max_length)
        if not text:
            return

        self._queue.submit_text(text)

    def _on_play_audio(self, msg: String):
        """음성 대화 오디오 조각 한 개.

        봉투가 깨졌거나 스풀 밖을 가리키면 로그만 남기고 넘어갑니다.
        조각 하나 때문에 스피커가 멈추면 대화 전체가 끊깁니다.
        """
        if not self._enabled:
            return

        try:
            request = parse_play_request(msg.data)
        except ValueError as exc:
            self.get_logger().error(f"재생 봉투가 잘못됐습니다: {exc}")
            return

        try:
            resolved = resolve_spool_path(self._audio_spool_dir, request.path)
        except ValueError as exc:
            self.get_logger().error(f"재생 경로를 거부했습니다: {exc}")
            return

        # 검증해 펼친 절대경로로 바꿔 넣습니다 — 재생 시점에 다시 풀지
        # 않으려는 것입니다(그 사이 심볼릭 링크가 바뀔 수 있습니다).
        decision = self._queue.submit_audio(
            dataclasses.replace(request, path=str(resolved))
        )
        self._log_decision(request, decision)

    def _on_cancel(self, msg: String):
        """대화가 끝났으니 남은 답변을 그칩니다.

        사용자가 페이지를 떠났는데 로봇이 혼자 계속 읊으면 이상합니다.
        인사말은 남깁니다 — cancel_all 이 오디오만 걷어냅니다.
        """
        dropped = self._queue.cancel_all()
        reason = (msg.data or "").strip() or "대화 종료"
        self.get_logger().info(f"재생을 중단합니다 ({reason}, 버린 조각 {dropped}개)")

    def _log_decision(self, request, decision):
        label = f"{request.turn_id}#{request.seq}"
        log = self.get_logger()

        if decision is PlaybackDecision.ACCEPTED:
            log.debug(f"조각 수신: {label}")
        elif decision is PlaybackDecision.PREEMPTED:
            log.info(
                f"새 질문이 들어와 이전 답변을 중단합니다 "
                f"(버린 조각 {self._queue.last_dropped}개) -> {label}"
            )
        elif decision is PlaybackDecision.DUPLICATE:
            log.debug(f"이미 받은 조각이라 무시합니다: {label}")
        elif decision is PlaybackDecision.STALE:
            log.warn(f"지난 조각이라 버립니다: {label}")
        elif decision is PlaybackDecision.OVERFLOW:
            log.warn(f"재생이 밀려서 버립니다: {label}")

    # --- 재생 ---

    def _playback_loop(self):
        while not self._stop.is_set():
            job = self._queue.pop(0.2)
            if job is None:
                continue
            self._play(job)

    def _play(self, job):
        if job.kind == "audio":
            self._play_audio(job)
        else:
            self._speak(job.payload)

    def _speak(self, text):
        """문장을 즉석 합성해 말합니다 (인사말). 선점 대상이 아닙니다."""
        argv = self._resolve_command(text)
        if argv is None:
            return
        self._run_player(argv, job=None, what=f"'{text[:20]}...'")

    def _play_audio(self, job):
        """합성해 둔 오디오 파일 한 조각을 재생합니다."""
        try:
            argv = build_command(self._audio_player, job.payload)
        except ValueError as exc:
            self.get_logger().error(f"재생 명령 조립 실패: {exc}")
            return

        self._run_player(argv, job=job, what=job.label())

    def _run_player(self, argv, job, what):
        """재생기를 띄우고 끝나거나 선점될 때까지 지킵니다.

        shell=True 를 쓰지 않습니다. 문장과 파일명은 서버 LLM 이 만든 외부
        문자열이라, 셸에 넘기면 임의 명령이 실행될 수 있습니다.
        """
        try:
            process = subprocess.Popen(argv)
        except FileNotFoundError:
            self.get_logger().error(
                f"{argv[0]} 를 찾을 수 없습니다. "
                "sudo apt install alsa-utils espeak-ng 또는 "
                "tts_command/audio_player 파라미터를 고치세요."
            )
            return
        except OSError as exc:
            self.get_logger().error(f"재생기를 띄우지 못했습니다: {exc}")
            return

        deadline = time.monotonic() + PLAYBACK_TIMEOUT_SEC
        while True:
            try:
                returncode = process.wait(timeout=CANCEL_POLL_SEC)
                break
            except subprocess.TimeoutExpired:
                pass

            if job is not None and not self._queue.is_current(job):
                self._terminate(process)
                self.get_logger().info(f"재생을 중단했습니다: {what}")
                return
            if self._stop.is_set():
                self._terminate(process)
                return
            if time.monotonic() > deadline:
                self._terminate(process)
                self.get_logger().warn(
                    f"재생이 {PLAYBACK_TIMEOUT_SEC}초를 넘겨 중단했습니다: {what}"
                )
                return

        if returncode != 0:
            # aplay 는 파일이 없거나 형식이 어긋나면 0 이 아닌 코드로 끝납니다.
            self.get_logger().error(
                f"재생 실패 (종료코드 {returncode}): {what}. "
                "aplay -l 로 사운드카드가 잡히는지 확인하세요."
            )

    def _terminate(self, process):
        """재생기를 정리합니다. 안 죽으면 강제로 끊습니다."""
        process.terminate()
        try:
            process.wait(timeout=1.0)
        except subprocess.TimeoutExpired:
            process.kill()
            try:
                process.wait(timeout=1.0)
            except subprocess.TimeoutExpired:
                self.get_logger().error("재생기가 종료되지 않습니다")

    def _resolve_command(self, text):
        """미리 녹음한 파일이 있으면 재생, 없으면 합성 명령을 만듭니다."""
        wav = self._find_wav(text)
        template = self._wav_player if wav else self._tts_command
        payload = str(wav) if wav else text

        try:
            return build_command(template, payload)
        except ValueError as exc:
            self.get_logger().error(f"명령 조립 실패: {exc}")
            return None

    def _find_wav(self, text):
        if not self._wav_dir:
            return None
        candidate = Path(self._wav_dir) / wav_filename(text)
        return candidate if candidate.is_file() else None

    def destroy_node(self):
        self._stop.set()
        self._queue.wake()
        self._worker.join(timeout=2.0)
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
