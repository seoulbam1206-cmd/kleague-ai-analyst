"""LLM이 만든 SQL 검사기 (AST 기반).

기존 방식(정규식 금지어 스캔)의 한계:
  - 'INTO' 같은 단어를 문자열 안에서 써도 오탐 / 주석·대소문자 우회 가능
  - 테이블 제한이 없어 시스템 테이블(INFORMATION_SCHEMA 등) 조회 가능
  - LIMIT 이 없어 수십만 행 조회로 앱이 멈출 수 있음

개선:
  1) sqlglot 으로 SQL을 '구문 트리'로 해석 → 문장 1개 + 최상위가 조회(Query)인지 확인
  2) 트리 안에 쓰기/DDL/INTO/잠금 노드가 하나라도 있으면 차단
  3) 테이블 화이트리스트 (설명서에 있는 테이블 + CTE 이름만 허용, schema.table 금지)
  4) 위험 함수 차단 (SLEEP, BENCHMARK, LOAD_FILE ...)
     + 서버·세션 정보 함수 차단 (VERSION, DATABASE, USER ...). sqlglot 이 이름을 바꿔 읽는 경우
       (VERSION → CURRENT_VERSION, DATABASE → CURRENT_SCHEMA)도 구문 노드 종류와 바뀐 이름 양쪽으로 잡는다.
     + 변수 차단: @@시스템변수(@@version, @@hostname), @사용자변수, 바인드 자리표시자(?, :a)
  5) LIMIT 없으면 추가, 너무 크면 상한으로 낮춤
  6) 함수 허용 목록 (기록만): 목록 밖 함수가 나오면 막지 않고 로그(kleague.sql_guard)에만 남긴다.
     정상 질문에 목록 밖 함수가 나오는지 지켜본 뒤 차단으로 바꿀지 정한다 (2026-11-05 도입).
DB 계층의 읽기 전용 계정·시간 제한과 함께 3중 방어를 이룬다.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError, TokenError
from sqlglot.helper import camel_to_snake_case

MAX_SQL_CHARS = 8000
log = logging.getLogger("kleague.sql_guard")

# 함수 허용 목록 (기록만 하는 방식). 이름은 sqlglot 이 읽은 이름 기준 (_func_names 의 이름 중 하나라도 맞으면 목록 안).
#   - 여기에 없어도 차단하지 않는다. GuardResult.unknown_funcs 와 경고 로그로만 남긴다.
#   - 차단은 지금처럼 _DENY_FUNCS·_INFO_NODES 가 한다.
ALLOWED_FUNCS = frozenset({
    # 논리·조건·형변환 (sqlglot 은 AND/OR/CASE 도 함수 노드로 읽는다)
    "AND", "OR", "XOR", "NOT", "CASE", "IF", "IIF", "COALESCE", "IFNULL", "NULLIF", "NVL", "EXISTS",
    "CAST", "TRY_CAST", "CONVERT", "TYPEOF",
    # 집계
    "COUNT", "SUM", "AVG", "MAX", "MIN", "TOTAL", "GROUP_CONCAT", "STDDEV", "STDDEV_POP", "STDDEV_SAMP",
    "VARIANCE", "VARIANCE_POP", "ANY_VALUE",
    # 수학
    "ROUND", "FLOOR", "CEIL", "CEILING", "ABS", "MOD", "POWER", "POW", "SQRT", "SIGN", "TRUNC", "TRUNCATE",
    "GREATEST", "LEAST", "LN", "LOG", "EXP", "SAFE_DIVIDE",
    # 문자열
    "CONCAT", "CONCAT_WS", "DPIPE", "SUBSTRING", "SUBSTR", "LEFT", "RIGHT", "LENGTH", "CHAR_LENGTH", "UPPER", "LOWER",
    "TRIM", "LTRIM", "RTRIM", "REPLACE", "STR_POSITION", "INSTR", "LOCATE", "PAD", "LPAD", "RPAD", "REVERSE",
    "FORMAT", "PRINTF", "NUMBER_TO_STR", "CHR", "CHAR", "UNICODE",
    # 날짜·시간
    "CURRENT_DATE", "CURRENT_TIME", "CURRENT_TIMESTAMP", "NOW", "CURDATE", "DATE", "TIME", "DATETIME",
    "JULIANDAY", "UNIXEPOCH", "UNIX_TIMESTAMP", "STRFTIME", "TIME_TO_STR", "STR_TO_DATE", "STR_TO_TIME",
    "TIME_FORMAT", "DATE_FORMAT", "TS_OR_DS_TO_DATE", "TS_OR_DS_TO_TIMESTAMP", "TS_OR_DS_ADD",
    "YEAR", "MONTH", "DAY", "DAY_OF_MONTH", "DAY_OF_WEEK", "DAY_OF_YEAR", "WEEK", "WEEK_OF_YEAR", "WEEKDAY",
    "DAYNAME", "QUARTER", "HOUR", "MINUTE", "SECOND", "EXTRACT", "LAST_DAY", "MAKEDATE",
    "DATEDIFF", "DATE_DIFF", "TIMESTAMPDIFF", "TIMESTAMP_DIFF", "DATE_ADD", "DATE_SUB", "DATE_TRUNC",
    # 순위·창 함수
    "RANK", "DENSE_RANK", "ROW_NUMBER", "NTILE", "PERCENT_RANK", "CUME_DIST", "LAG", "LEAD",
    "FIRST_VALUE", "LAST_VALUE", "NTH_VALUE",
})

_DENY_FUNCS = {
    # 지연·부하·잠금·파일
    "SLEEP", "BENCHMARK", "LOAD_FILE", "GET_LOCK", "RELEASE_LOCK", "IS_FREE_LOCK", "IS_USED_LOCK",
    "RELEASE_ALL_LOCKS", "MASTER_POS_WAIT", "SOURCE_POS_WAIT", "PG_SLEEP",
    "LOAD_EXTENSION", "READFILE", "WRITEFILE", "RANDOMBLOB", "ZEROBLOB",
    # 서버·세션 정보 (원래 이름)
    "USER", "CURRENT_USER", "SESSION_USER", "SYSTEM_USER", "CURRENT_ROLE",
    "DATABASE", "SCHEMA", "VERSION", "CONNECTION_ID", "LAST_INSERT_ID", "ROW_COUNT", "FOUND_ROWS",
    "SQLITE_VERSION", "SQLITE_SOURCE_ID", "SQLITE_COMPILEOPTION_GET", "SQLITE_COMPILEOPTION_USED",
    "SQLITE_OFFSET", "LAST_INSERT_ROWID", "CHANGES", "TOTAL_CHANGES",
    "TIDB_VERSION", "TIDB_IS_DDL_OWNER", "TIDB_DECODE_PLAN", "TIDB_DECODE_KEY", "TIDB_DECODE_SQL_DIGESTS",
    "TIDB_CURRENT_TSO", "TIDB_PARSE_TSO", "TIDB_PARSE_TSO_LOGICAL", "TIDB_ROW_CHECKSUM",
    "CURRENT_RESOURCE_GROUP",
    # sqlglot 이 바꿔 읽은 이름 (VERSION/sqlite_version → CURRENT_VERSION, DATABASE/SCHEMA → CURRENT_SCHEMA ...)
    "CURRENT_VERSION", "CURRENT_SCHEMA", "CURRENT_SCHEMAS", "CURRENT_DATABASE", "CURRENT_CATALOG",
    "CURRENT_USER_ID", "CURRENT_SESSION",
}


def _node_types(*names: str) -> tuple[type, ...]:
    """sqlglot 버전에 따라 없는 클래스가 있어도 동작하도록."""
    return tuple(getattr(exp, n) for n in names if hasattr(exp, n))


# 서버·세션 정보를 돌려주는 구문 노드. 이름이 어떻게 바뀌어도 노드 종류로 잡는다.
# (CurrentDate·CurrentTime·CurrentTimestamp 같은 날짜 함수는 정상 질문에 쓰여 넣지 않음)
_INFO_NODES = _node_types(
    "CurrentVersion", "CurrentSchema", "CurrentSchemas", "CurrentDatabase", "CurrentCatalog",
    "CurrentUser", "CurrentUserId", "SessionUser", "CurrentRole", "CurrentRoleType", "CurrentAvailableRoles",
    "CurrentSecondaryRoles", "CurrentAccount", "CurrentAccountName", "CurrentClient", "CurrentIpAddress",
    "CurrentOrganizationName", "CurrentOrganizationUser", "CurrentRegion", "CurrentSession",
    "CurrentStatement", "CurrentTransaction", "CurrentWarehouse", "DatabaseProperty",
)

# 변수: @@시스템변수(SessionParameter), @사용자변수(Parameter), 바인드 자리표시자(Placeholder)
_VARIABLE_NODES = _node_types("SessionParameter", "Parameter", "Placeholder")

_FORBIDDEN_NODES = _node_types(
    "Insert", "Update", "Delete", "Merge", "Drop", "Create", "Alter", "AlterTable",
    "Command", "Into", "Lock", "Set", "Pragma", "Transaction", "Commit", "Rollback",
    "Use", "Grant", "Revoke", "Copy", "LoadData", "Attach", "Detach", "TruncateTable",
)


@dataclass
class GuardResult:
    ok: bool
    sql: str = ""
    reason: str = ""
    limit_applied: bool = False
    unknown_funcs: tuple[str, ...] = ()   # 허용 목록 밖 함수 (기록만, 차단하지 않음)


def _func_name(f: exp.Func) -> str:
    if isinstance(f, exp.Anonymous):
        return str(f.name).upper()
    try:
        return f.sql_name().upper()
    except Exception:
        return type(f).__name__.upper()


def _func_names(f: exp.Func) -> set[str]:
    """함수 하나를 부를 수 있는 이름 전부: 원래 이름 + sqlglot 이 바꾼 이름 + 노드 이름(snake)."""
    names = {_func_name(f)}
    if isinstance(f, exp.Anonymous):
        return names
    cls = type(f)
    names.add(camel_to_snake_case(cls.__name__).upper())
    for n in getattr(cls, "_sql_names", None) or []:
        names.add(str(n).upper())
    return names


def check_sql(sql: str, allowed_tables: frozenset[str] | set[str], dialect: str,
              max_rows: int) -> GuardResult:
    if not sql or not sql.strip():
        return GuardResult(False, reason="SQL이 비어 있습니다.")
    if len(sql) > MAX_SQL_CHARS:
        return GuardResult(False, reason="SQL이 너무 깁니다.")

    try:
        statements = [s for s in sqlglot.parse(sql, read=dialect) if s is not None]
    except (ParseError, TokenError) as e:
        return GuardResult(False, reason=f"SQL 문법을 해석할 수 없습니다: {str(e)[:200]}")

    if len(statements) != 1:
        return GuardResult(False, reason="SELECT 문장은 하나만 허용됩니다.")
    tree = statements[0]

    if not isinstance(tree, exp.Query):
        return GuardResult(False, reason="조회(SELECT) 문만 실행할 수 있습니다.")

    bad = tree.find(*_FORBIDDEN_NODES)
    if bad is not None:
        return GuardResult(False, reason=f"허용되지 않는 구문이 포함되어 있습니다: {type(bad).__name__}")

    var = tree.find(*_VARIABLE_NODES)
    if var is not None:
        return GuardResult(False, reason="변수(@, @@, ?)는 허용되지 않습니다.")

    info = tree.find(*_INFO_NODES)
    if info is not None:
        return GuardResult(False, reason="서버·세션 정보 함수는 허용되지 않습니다.")

    cte_names = {c.alias_or_name.lower() for c in tree.find_all(exp.CTE)}
    allowed = {t.lower() for t in allowed_tables}
    for t in tree.find_all(exp.Table):
        name = (t.name or "").lower()
        if not name:
            continue
        if t.args.get("db") or t.args.get("catalog"):
            return GuardResult(False, reason=f"스키마를 지정한 테이블은 허용되지 않습니다: {t.sql()}")
        if name in cte_names:
            continue
        if name not in allowed:
            return GuardResult(False, reason=f"허용되지 않은 테이블입니다: {name}")

    for f in tree.find_all(exp.Func):
        hit = _func_names(f) & _DENY_FUNCS
        if hit:
            return GuardResult(False, reason=f"허용되지 않은 함수입니다: {_func_name(f)}")

    # LIMIT 강제
    limit_applied = False
    lim = tree.args.get("limit")
    if lim is None:
        tree = tree.limit(max_rows)
        limit_applied = True
    else:
        val_node = lim.args.get("expression") or lim.this
        try:
            val = int(val_node.name) if val_node is not None else None
        except (TypeError, ValueError):
            val = None
        if val is None or val > max_rows:
            lim.set("expression", exp.Literal.number(max_rows))
            limit_applied = True

    # 함수 허용 목록: 기록만 한다 (차단하지 않음). 기존 판정·SQL 은 그대로.
    unknown = sorted({_func_name(f) for f in tree.find_all(exp.Func) if not (_func_names(f) & ALLOWED_FUNCS)})
    if unknown:
        log.warning("허용 목록 밖 함수 (기록만, 차단 안 함): %s", ", ".join(unknown))

    return GuardResult(True, sql=tree.sql(dialect=dialect), limit_applied=limit_applied,
                       unknown_funcs=tuple(unknown))
