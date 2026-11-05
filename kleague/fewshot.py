"""유사 질문 예시 검색 (Few-shot).

기존: sentence-transformers(약 400MB 모델) + ChromaDB 로컬 파일
  → Streamlit Cloud 메모리 부담, 첫 로딩 수십 초, 재배포 시 벡터DB 초기화
개선: 글자 n-gram TF-IDF + 코사인 유사도 (scikit-learn)
  → 모델 다운로드 0, 수백 개 예시 기준 검색 1ms 내외
  → 한국어 조사 변화('득점왕은'/'득점왕')에도 강함 (글자 단위)
  → 예시는 DB(app_sql_example)에 승인된 것만 사용 → 재배포해도 유지
"""
from __future__ import annotations

import hashlib
import threading
from dataclasses import dataclass

from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import linear_kernel


@dataclass(frozen=True)
class Example:
    question: str
    sql: str


class ExampleIndex:
    def __init__(self, min_score: float = 0.35, top_k: int = 3):
        self.min_score = min_score
        self.top_k = top_k
        self._lock = threading.Lock()
        self._sig = ""
        self._examples: list[Example] = []
        self._vec = None
        self._mat = None

    def fit(self, examples: list[Example]) -> None:
        sig = hashlib.sha1("\n".join(e.question + e.sql for e in examples).encode()).hexdigest()
        with self._lock:
            if sig == self._sig:
                return  # 변경 없으면 재학습 안 함
            self._examples = list(examples)
            if examples:
                self._vec = TfidfVectorizer(analyzer="char_wb", ngram_range=(2, 4), sublinear_tf=True)
                self._mat = self._vec.fit_transform([_norm(e.question) for e in examples])
            else:
                self._vec, self._mat = None, None
            self._sig = sig

    def search(self, question: str) -> list[tuple[Example, float]]:
        with self._lock:
            if self._vec is None or not self._examples:
                return []
            q = self._vec.transform([_norm(question)])
            scores = linear_kernel(q, self._mat).ravel()
            order = scores.argsort()[::-1][: self.top_k]
            return [(self._examples[i], float(scores[i])) for i in order
                    if scores[i] >= self.min_score]


def _norm(text: str) -> str:
    return " ".join(text.lower().split())


def format_examples(hits: list[tuple[Example, float]]) -> str:
    if not hits:
        return ""
    parts = ["[참고 예시] 비슷한 질문의 검증된 SQL입니다. 구조를 참고하되 질문에 맞게 조건을 바꾸세요."]
    for i, (ex, score) in enumerate(hits, 1):
        parts.append(f"예시{i} (유사도 {score:.2f})\n질문: {ex.question}\nSQL: {ex.sql}")
    return "\n\n".join(parts)
