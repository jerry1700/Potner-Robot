"""potner_llm 터미널 채팅 CLI(cli.py)의 헬퍼 단위 테스트 (ROS/API 키 불필요).

네트워크·stdin을 쓰지 않는다 — main()의 대화 루프는 다루지 않고,
.env 로딩 / config 파싱 / 센서 provider 조립 / argparse만 검증한다.
실제 src/potner_llm/data/ 파일은 건드리지 않고 tmp_path만 쓴다.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from potner_llm import cli
from potner_llm.sensor_provider import FileSensorSource, SensorDataProvider


# --- _load_env_file: .env를 직접 파싱해 os.environ에 싣는다 ---


def test_load_env_file_sets_new_vars(tmp_path, monkeypatch):
    monkeypatch.delenv("POTNER_CLI_TEST_KEY", raising=False)
    env = tmp_path / ".env"
    env.write_text("POTNER_CLI_TEST_KEY=hello\n", encoding="utf-8")

    cli._load_env_file(env)

    assert os.environ["POTNER_CLI_TEST_KEY"] == "hello"
    monkeypatch.delenv("POTNER_CLI_TEST_KEY", raising=False)


def test_load_env_file_does_not_override_existing(tmp_path, monkeypatch):
    """이미 셸에서 export된 값이 .env보다 우선한다."""
    monkeypatch.setenv("POTNER_CLI_TEST_KEY", "from-shell")
    env = tmp_path / ".env"
    env.write_text("POTNER_CLI_TEST_KEY=from-file\n", encoding="utf-8")

    cli._load_env_file(env)

    assert os.environ["POTNER_CLI_TEST_KEY"] == "from-shell"


def test_load_env_file_skips_comments_blanks_and_malformed(tmp_path, monkeypatch):
    """주석·빈 줄·'=' 없는 줄·빈 값은 조용히 건너뛴다."""
    for key in ("POTNER_CLI_A", "POTNER_CLI_B", "POTNER_CLI_EMPTY"):
        monkeypatch.delenv(key, raising=False)
    env = tmp_path / ".env"
    env.write_text(
        "\n".join(
            [
                "# 주석 줄",
                "",
                "이건 등호가 없는 줄",
                "POTNER_CLI_A = spaced ",
                "POTNER_CLI_EMPTY=",
                "POTNER_CLI_B=b",
            ]
        ),
        encoding="utf-8",
    )

    cli._load_env_file(env)

    assert os.environ["POTNER_CLI_A"] == "spaced"
    assert os.environ["POTNER_CLI_B"] == "b"
    # 값이 빈 문자열이면 아예 설정하지 않는다 (현재 동작).
    assert "POTNER_CLI_EMPTY" not in os.environ
    for key in ("POTNER_CLI_A", "POTNER_CLI_B"):
        monkeypatch.delenv(key, raising=False)


def test_load_env_file_missing_file_is_noop(tmp_path):
    """파일이 없으면 예외 없이 그냥 돌아온다."""
    cli._load_env_file(tmp_path / "no-such.env")  # 예외가 없으면 통과


def test_load_env_file_keeps_quotes_and_inline_comments(tmp_path, monkeypatch):
    """dotenv와 달리 따옴표 제거·인라인 주석 제거를 하지 않는다 (현재 동작 기록)."""
    monkeypatch.delenv("POTNER_CLI_QUOTED", raising=False)
    env = tmp_path / ".env"
    env.write_text('POTNER_CLI_QUOTED="v" # tail\n', encoding="utf-8")

    cli._load_env_file(env)

    assert os.environ["POTNER_CLI_QUOTED"] == '"v" # tail'
    monkeypatch.delenv("POTNER_CLI_QUOTED", raising=False)


# --- _load_config: YAML 파싱 ---


def test_load_config_parses_yaml(tmp_path):
    path = tmp_path / "llm.yaml"
    path.write_text("llm:\n  provider: gms\nsensor:\n  source: none\n", encoding="utf-8")

    config = cli._load_config(path)

    assert config == {"llm": {"provider": "gms"}, "sensor": {"source": "none"}}


def test_load_config_empty_file_returns_empty_dict(tmp_path):
    path = tmp_path / "empty.yaml"
    path.write_text("", encoding="utf-8")

    assert cli._load_config(path) == {}


# --- _build_sensor_provider: sensor 섹션 → provider, 없으면 파일 폴백 ---


def test_build_sensor_provider_empty_config_falls_back_to_package_file():
    provider = cli._build_sensor_provider({})

    assert isinstance(provider, SensorDataProvider)
    source = provider._source
    assert isinstance(source, FileSensorSource)
    assert source.path == cli.PACKAGE_DIR / "data" / "sensors.json"


def test_build_sensor_provider_source_none_falls_back_to_package_file():
    """sensor.source=none이면 create_sensor_provider가 None → 파일 폴백."""
    provider = cli._build_sensor_provider({"sensor": {"source": "none"}})

    source = provider._source
    assert isinstance(source, FileSensorSource)
    assert source.path == cli.PACKAGE_DIR / "data" / "sensors.json"


def test_build_sensor_provider_file_source_reads_given_path(tmp_path):
    data = tmp_path / "sensors.json"
    data.write_text(
        json.dumps({"soil": 42.0, "temp": 26.1, "humidity": 55.0, "light": 800.0}),
        encoding="utf-8",
    )

    provider = cli._build_sensor_provider({"sensor": {"source": "file", "path": str(data)}})
    snapshot = provider.snapshot()

    assert snapshot is not None
    assert snapshot.soil == 42.0
    assert snapshot.temp == 26.1
    assert snapshot.humidity == 55.0
    assert snapshot.light == 800.0


# --- _build_parser: 기본값과 옵션 파싱 ---


def test_build_parser_defaults_point_under_repo():
    args = cli._build_parser().parse_args([])

    assert Path(args.env_file) == cli.REPO_ROOT / ".env"
    assert Path(args.config) == cli.PACKAGE_DIR / "config" / "llm.yaml"
    assert args.session_id is None
    assert args.reset is False


def test_build_parser_accepts_session_id_and_reset():
    args = cli._build_parser().parse_args(["--session-id", "device-7", "--reset"])

    assert args.session_id == "device-7"
    assert args.reset is True


def test_build_parser_rejects_unknown_option():
    with pytest.raises(SystemExit):
        cli._build_parser().parse_args(["--no-such-flag"])
