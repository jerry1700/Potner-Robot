"""potner_llm 터미널 채팅 CLI — 직접 쳐보면서 동작을 확인하기 위한 도구.

ROS 없이 돌아간다 (rclpy import 안 함). 실행:

    python src/potner_llm/potner_llm/cli.py

종료(exit/quit/Ctrl+D)하면 그 대화를 conversation 백엔드에 저장하고,
다음에 다시 실행하면 그 대화를 불러와서 이어서 대화한다.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path
from typing import Any

import yaml

from .client import describe_llm
from .conversation_backend import create_conversation_backend
from .dialogue import DialogueService
from .events import EventStore
from .sensor_provider import (
    FileSensorSource,
    SensorDataProvider,
    create_sensor_provider,
)

# .../src/potner_llm/potner_llm/cli.py 기준
PACKAGE_DIR = Path(__file__).resolve().parents[1]  # .../src/potner_llm
REPO_ROOT = Path(__file__).resolve().parents[3]  # 워크스페이스 루트 (.env 위치)


def _load_env_file(path: Path) -> None:
    """potner_llm은 python-dotenv에 의존하지 않으므로 .env를 직접 읽는다."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip()
        if value and key not in os.environ:
            os.environ[key] = value


def _load_config(path: Path) -> dict[str, Any]:
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def _build_sensor_provider(config: dict[str, Any]) -> SensorDataProvider:
    """config의 sensor: 섹션으로 고른다. 없으면 data/sensors.json 파일 폴백 —
    값을 바꿔가며 응답 변화를 확인할 수 있고, 파일이 없거나 깨져도 provider가
    흡수(미측정 라벨)한다."""
    return create_sensor_provider(config) or SensorDataProvider(
        FileSensorSource(PACKAGE_DIR / "data" / "sensors.json")
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="potner_llm 터미널 채팅 (테스트용, ROS 불필요)")
    parser.add_argument("--env-file", default=str(REPO_ROOT / ".env"))
    parser.add_argument("--config", default=str(PACKAGE_DIR / "config" / "llm.yaml"))
    parser.add_argument(
        "--session-id", default=None, help="conversation.session_id 오버라이드 (기기/사용자별 구분)"
    )
    parser.add_argument(
        "--reset", action="store_true", help="이전에 저장된 대화를 무시하고 새로 시작"
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    _load_env_file(Path(args.env_file))
    config = _load_config(Path(args.config))

    backend = create_conversation_backend(config, default_dir=PACKAGE_DIR / "data")
    conv_cfg = config.get("conversation") or {}
    session_id = args.session_id or str(conv_cfg.get("session_id", "default"))
    if args.reset:
        backend.save(session_id, [])

    store = EventStore(PACKAGE_DIR / "data" / "events.jsonl")
    sensor_provider = _build_sensor_provider(config)

    service = DialogueService(
        config,
        get_status=sensor_provider.status,
        event_store=store,
        conversation_backend=backend,
        session_id=session_id,
    )

    print(describe_llm(service.llm, config))
    prior_turns = len(service.history) // 2
    if prior_turns:
        print(f"(이전 대화 {prior_turns}턴을 불러왔습니다. 이어서 대화하세요.)")
    print(f"session_id={session_id} — 종료: exit / quit / Ctrl+D\n")

    try:
        while True:
            try:
                text = input("you> ").strip()
            except EOFError:
                print()
                break
            if not text:
                continue
            if text.lower() in {"exit", "quit"}:
                break
            reply = service.chat_once(text)
            print(f"potner> {reply}\n")
    except KeyboardInterrupt:
        print()
    finally:
        service.end_conversation()
        print("대화를 저장했습니다. 다음 실행에서 이어집니다.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
