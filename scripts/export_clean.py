"""새 공개 저장소용 깨끗한 복사본 만들기 (git 기록 없이 파일만).

    python scripts/export_clean.py                       # → ../kleague-ai-publish/
    python scripts/export_clean.py --dest D:/somewhere   # 다른 위치

- 이 폴더의 .git 은 읽기만 한다 (git ls-files 로 .gitignore 대상을 거름). git init·push 는 하지 않는다.
- 빼는 것: .git · .gitignore 대상 · data/ · samples/ · _old/ · .api_cache/ · .env · *.bak · *.sqlite(가상 데이터 demo/demo.sqlite 만 예외) ·
  연맹 로고 이미지 · 보류 파일(scripts/prepublish_check.py 의 HOLD)
- 순서: ① 원본에서 prepublish_check → 실패면 아무것도 만들지 않음 ② 복사 ③ 복사본을 다시 점검
- 대상 폴더가 이미 있고 비어 있지 않으면 멈춘다 (지우지 않음). 직접 지운 뒤 다시 실행할 것.
"""
from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts import prepublish_check as P  # noqa: E402

DEFAULT_DEST = ROOT.parent / "kleague-ai-publish"


def export(dest: Path, src: Path = ROOT, log=print) -> int:
    files, dropped = P.publish_files(src)
    checks = P.run_checks(src, files, dropped)
    if not P.passed(checks):
        log(P.render(checks, len(files)))
        log("\n원본 점검 실패 → 복사본을 만들지 않았습니다.")
        return 1
    dest = dest.resolve()
    if dest == src.resolve() or src.resolve() in dest.parents:
        log("대상 폴더가 원본 안에 있습니다. 다른 위치를 지정하세요.")
        return 2
    if dest.exists() and any(dest.iterdir()):
        log(f"대상 폴더가 비어 있지 않습니다: {dest}\n지우지 않고 멈춥니다. 직접 비운 뒤 다시 실행하세요.")
        return 2
    for rel in files:
        out = dest / rel
        out.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src / rel, out)
    log(f"복사: {len(files)}개 파일 → {dest}")
    if dropped:
        log("뺀 파일: " + ", ".join(f"{k} [{v}]" for k, v in sorted(dropped.items())))

    after = P.run_checks(dest)
    log("\n[복사본 점검]")
    log(P.render(after, len(P.target_files(dest)[0])))
    if not P.passed(after):
        log("\n복사본 점검 실패 – 위 ❌ 항목을 확인하세요 (복사본은 그대로 남겨 둠).")
        return 1
    log("\n복사본 점검 통과. git init·push 는 하지 않았습니다.")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="공개 저장소용 깨끗한 복사본")
    ap.add_argument("--dest", default=str(DEFAULT_DEST))
    args = ap.parse_args(argv)
    return export(Path(args.dest))


if __name__ == "__main__":
    sys.exit(main())
