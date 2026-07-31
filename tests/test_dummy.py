# CI 파이프라인에서 pytest가 실행될 때, 수집할 테스트가 하나도 없으면 에러(Exit code 5)를 냅니다.
# 이를 방지하기 위해 언제나 성공하는 기본 더미(Dummy) 테스트를 추가합니다.


def test_ci_pipeline_ready():
    """
    CI 환경이 올바르게 설정되었는지 확인하는 기본 테스트입니다.
    이 테스트가 통과하면 pytest가 정상 작동하는 것입니다.
    """
    assert True
