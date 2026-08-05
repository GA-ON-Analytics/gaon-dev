"""intent 표가 어긋나면 import가 터지는지 확인한다.

새 intent를 _SUPPORTED_INTENTS에만 추가하고 _INTENT_TOOL을 안 고치는 실수를 재현한다.
예전 삼항식이었다면 그 intent가 조용히 run_simulation으로 흘러갔을 상황이다.
"""
import pathlib
import subprocess
import sys

PATH = pathlib.Path("backend/llm_poc/chat_service.py")
NEEDLE = '    "unsupported",\n}\n# Tool을 부르지'
INJECT = '    "unsupported",\n    "policy_ranking",\n}\n# Tool을 부르지'

src = PATH.read_text(encoding="utf-8")
assert NEEDLE in src, "주입 지점을 못 찾았다 — 코드가 바뀌었는지 확인할 것"

PATH.write_text(src.replace(NEEDLE, INJECT, 1), encoding="utf-8")
try:
    r = subprocess.run(
        [sys.executable, "-c", "import backend.llm_poc.chat_service"],
        capture_output=True, text=True, encoding="utf-8",
    )
    print(f"exit code: {r.returncode}  (0이면 가드가 안 걸린 것 = 실패)")
    last = [ln for ln in (r.stderr or "").splitlines() if ln.strip()]
    print("마지막 오류:", last[-1] if last else "(없음)")
finally:
    PATH.write_text(src, encoding="utf-8")
    print("원본 복구 완료:", PATH.read_text(encoding="utf-8") == src)
