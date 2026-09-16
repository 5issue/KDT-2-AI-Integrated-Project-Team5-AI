"""판정 모델 설정 검증. 자가 평가를 조립 시점에 막는 것이 핵심입니다.

`ai_context/사람이 쓴 문서/rag 평가 지표 종류.md` 5.2 - 평가 대상과 같은 계열 모델을
judge 로 쓰면 self-preference bias 가 걸립니다. 실제로 `gpt-4.1-mini` 가 자기 문구를
채점했을 때 문구가 좋아졌는데 통과율이 내려갔습니다.
"""

from __future__ import annotations

import pytest

from rag_lab.config import Settings


def settings(**overrides: object) -> Settings:
    """`.env` 를 읽지 않는 설정 하나. 테스트가 로컬 환경에 좌우되지 않게 합니다."""
    base: dict[str, object] = {
        "_env_file": None,
        "llm_provider": "openrouter",
        "llm_api_key": "sk-chat",
        "llm_model": "openai/gpt-4.1-mini",
    }
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


def test_empty_judge_model_refuses_to_run() -> None:
    """기본값을 채팅 모델로 두면 아무도 모르는 채 자가 평가가 됩니다."""
    with pytest.raises(RuntimeError, match="JUDGE_MODEL"):
        settings().require_judge_model()


def test_same_model_and_provider_is_rejected() -> None:
    """같은 서버의 같은 모델이 자기 답을 채점하는 것을 막습니다."""
    with pytest.raises(RuntimeError, match="생성 모델과 같습니다"):
        settings(judge_model="openai/gpt-4.1-mini").require_judge_model()


def test_a_different_model_on_the_same_provider_is_allowed() -> None:
    """OpenRouter 안에서 상위 모델로 올리는 것이 가장 흔한 조합입니다."""
    assert settings(judge_model="openai/gpt-4.1").require_judge_model() == "openai/gpt-4.1"


def test_same_model_on_a_different_provider_is_still_rejected() -> None:
    """**서버가 달라도 모델이 같으면 자가 평가입니다.**

    `openai/gpt-4.1-mini`(OpenRouter 경유)와 `gpt-4.1-mini`(OpenAI 직접)는 경로만 다를 뿐
    같은 모델이라 self-preference bias 가 그대로 걸립니다. 처음에는 (공급자, 모델, 주소)가
    전부 같을 때만 막았는데 그건 빠져나갈 구멍이었습니다.
    """
    with pytest.raises(RuntimeError, match="생성 모델과 같습니다"):
        settings(judge_provider="openai", judge_model="gpt-4.1-mini", judge_api_key="sk-judge").require_judge_model()

    with pytest.raises(RuntimeError, match="생성 모델과 같습니다"):
        settings(
            judge_provider="openai", judge_model="openai/gpt-4.1-mini", judge_api_key="sk-judge"
        ).require_judge_model()


def test_model_identity_ignores_case_and_vendor_prefix() -> None:
    """`GPT-4.1-MINI` 로 적어도 같은 모델입니다."""
    with pytest.raises(RuntimeError, match="생성 모델과 같습니다"):
        settings(judge_model="OpenAI/GPT-4.1-Mini").require_judge_model()


def test_whitespace_does_not_sneak_past_the_guard() -> None:
    """공백만 다른 이름을 다른 모델로 세면 자가 평가가 통과합니다."""
    with pytest.raises(RuntimeError, match="생성 모델과 같습니다"):
        settings(judge_model="  openai/gpt-4.1-mini  ").require_judge_model()


def test_separate_provider_requires_its_own_key() -> None:
    """채팅 키를 물려주면 그 키가 다른 회사 엔드포인트로 그대로 나갑니다."""
    with pytest.raises(RuntimeError, match="JUDGE_API_KEY"):
        settings(judge_provider="openai", judge_model="gpt-4.1").require_judge_api_key()


def test_inheriting_the_chat_key_is_fine_on_the_same_provider() -> None:
    """공급자도 주소도 그대로면 같은 서버라, 키를 또 적게 할 이유가 없습니다."""
    assert settings(judge_model="openai/gpt-4.1").require_judge_api_key() == "sk-chat"


def test_judge_key_is_a_secret() -> None:
    """로그나 실험 기록에 값이 그대로 찍히면 안 됩니다."""
    resolved = settings(judge_model="openai/gpt-4.1", judge_api_key="sk-judge")
    assert "sk-judge" not in repr(resolved)
    assert "sk-judge" not in str(resolved.judge_api_key)


def test_same_provider_different_model_reuses_the_chat_key() -> None:
    """가장 흔한 조합입니다 - OpenRouter 안에서 한 단계 위 모델로.

    공급자를 적었다는 이유만으로 키를 또 받으면 같은 서버 키를 두 번 적게 됩니다.
    """
    resolved = settings(judge_provider="openrouter", judge_model="openai/gpt-4.1")
    assert resolved.judge_inherits_chat
    assert resolved.require_judge_api_key() == "sk-chat"


def test_different_base_url_requires_its_own_key() -> None:
    """공급자 이름이 같아도 주소가 다르면 다른 서버입니다."""
    resolved = settings(judge_provider="openrouter", judge_model="gpt-4.1", judge_base_url="https://proxy.example/v1")
    assert not resolved.judge_inherits_chat
    with pytest.raises(RuntimeError, match="JUDGE_API_KEY"):
        resolved.require_judge_api_key()
