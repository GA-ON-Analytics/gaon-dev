"""③ 착수 전 확인: 라우터 프롬프트가 컨텍스트를 얼마나 쓰고 있나.

새 intent를 추가하려면 SYSTEM_PROMPT에 설명을 넣어야 하는데, 남은 여유가 얼마인지
모르면 넣고 나서야 잘린 걸 알게 된다.
"""
from backend.llm_poc.chat_service import run_chat

r = run_chat("11110_00909 격자의 녹지율 알려줘", "11110_00909")
m = r.metrics if hasattr(r, "metrics") else {}
keys = [k for k in m if "count" in k or "token" in k or "second" in k]
print("측정 가능한 지표:")
for k in sorted(keys):
    print(f"  {k:<32} {m[k]}")

pe = m.get("prompt_eval_count")
if pe:
    print(f"\n라우터 프롬프트 {pe:,} 토큰 / 컨텍스트 4,096 → 여유 {4096 - pe:,} 토큰")
