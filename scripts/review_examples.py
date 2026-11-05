"""사용자 👍 로 쌓인 예시를 검토해 승인/거절 (관리자 전용, 내 PC에서 실행).

    python scripts/review_examples.py

승인된 예시만 Few-shot 에 쓰이므로, 틀린 SQL이 퍼지는 것을 막는다.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from kleague.config import load_settings  # noqa: E402
from kleague.db import Database  # noqa: E402
from kleague.store import AppStore  # noqa: E402


def main() -> None:
    s = load_settings()
    store = AppStore(Database(s.app_db_url, read_only=False))
    pending = store.pending_examples()
    if not pending:
        print("검토할 예시가 없습니다.")
        return
    for eid, q, sql in pending:
        print("=" * 70)
        print(f"[{eid}] 질문: {q}\n{sql}")
        ans = input("승인(y) / 거절(n) / 건너뛰기(Enter) / 종료(q): ").strip().lower()
        if ans == "q":
            break
        if ans in ("y", "n"):
            store.set_example_approved(eid, ans == "y")
            print("→ 승인" if ans == "y" else "→ 거절")


if __name__ == "__main__":
    main()
