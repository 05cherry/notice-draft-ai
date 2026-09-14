"""공지 유형(카테고리 > subtype) 정의 · 라우팅 · 입력 정규화/검증.

한 공지는 카테고리 1~2개로 이뤄진다(예: 안내+입출금 "유의촉구 및 입출금 일시 중단",
거래유의+거래지원종료 "A 지정 해제 및 B 거래지원 종료"). 카테고리마다 subtype을 하나씩
정하고(= 파트), 파트별 필수 입력을 검증한다.

subtype은 실제 공지 제목 분석(OpenSearch 4,237건)에서 반복 패턴이 확인된 것만 둔다.
반복 패턴이 없는 공지는 <카테고리>/general 로 처리한다(유사 공지 검색 + 사용자 입력 + 일반 지침).
  - 안내 '시세 급변동 거래 유의'(115건)는 2025년 이후 0건이라 subtype으로 두지 않았다.
유형을 늘리거나 필드를 바꾸려면 TYPES만 고친다. 작성 지침 문구는 prompts/ 파일로 둔다.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

MAX_CATEGORIES = 2
TARGET_CATEGORIES = ("입출금", "공시", "거래유의", "안내")   # 이번 단계 구현 범위

_CATEGORIES_PATH = Path(__file__).resolve().parents[2] / "data" / "categories.json"

# '아직 모름'을 명시한 값. 필수 검증은 통과시키되 초안에는 [확인 필요]/추후 안내로 쓰게 한다.
UNKNOWN_VALUES = {"미정", "미확정", "추후 공지", "추후공지", "추후 안내", "tbd"}

_WEEKDAYS = "월화수목금토일"


# ── 필드 ────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class Field:
    label: str
    kind: str = "text"      # text | coins | date | datetime | urls
    question: str = ""
    exact: bool = False     # 초안에 입력 그대로 나와야 하는 고유명(거래소명·서비스명 등)


def is_temporal(kind: str) -> bool:
    return kind in ("date", "datetime")


FIELDS: dict[str, Field] = {
    "coins": Field("대상 가상자산", "coins",
                   "대상 가상자산의 한글명과 티커를 알려주세요(여러 개 가능). 예) 메가이더(MEGA), 비너스(XVS)"),
    "reason": Field("사유", "text", "공지 사유를 한두 문장으로 알려주세요."),
    "network": Field("대상 네트워크", "text", "대상 네트워크(체인)를 알려주세요. 예) Ethereum, BNB Smart Chain",
                     exact=True),
    "suspend_at": Field("입출금 중지 시점", "datetime", "중지 일시를 알려주세요. 예) 2026-09-10 19:00"),
    "resume_at": Field("입출금 재개 시점", "datetime", "재개 일시가 정해졌나요? 모르면 '미정'."),
    "scope": Field("중지 범위", "text", "입출금 전체인가요, 입금 또는 출금만인가요?"),
    "upgrade_at": Field("업그레이드 예상 시점", "datetime", "네트워크 업그레이드 예상 일시. 예) 2026-09-11 03:00"),
    "occurred_at": Field("발생 시점", "datetime", "문제가 발생한 일시. 예) 2026-09-10 14:00"),
    "status": Field("현재 상태", "text", "현재 상태를 알려주세요. 예) 지연 중 / 정상화 완료"),
    "designated_at": Field("지정일", "date", "거래유의종목 지정일. 예) 2026-09-01"),
    "deposit_status": Field("입금·입출금 제한 사항", "text",
                            "입금 중지 등 제한이 있나요? 있으면 시점까지. 예) 입금 중지 2026-09-01 16:00"),
    "next_review": Field("지정 연장·해제 / 거래지원 종료 공지 일정", "text",
                         "예) 9월 3주차 예정(9/14 ~ 9/18 중)"),
    "released_at": Field("해제일", "date", "지정 해제일. 예) 2026-08-07"),
    "deposit_resume_at": Field("입금 재개 시점", "datetime", "입금 재개 일시. 예) 2026-08-07 16:00"),
    "seller": Field("매도 사업자", "text", "매도 계획/결과를 낸 가상자산사업자명. 예) (주)코빗", exact=True),
    "holding_coins": Field("보유 기준 가상자산", "coins", "어떤 가상자산 보유자에게 지급하나요? 예) 네오(NEO)"),
    "snapshot": Field("스냅샷 시점·기준", "text", "예) 매주 토요일 00:00(KST) 보유 수량 기준"),
    "round": Field("지급 차수·주차", "text", "예) 9월 2주차, 18차"),
    "paid_at": Field("지급일", "date", "지급(예정)일. 모르면 '미정'. 예) 2026-09-30"),
    "withdraw_open": Field("출금 오픈 시점", "datetime", "지급된 코인의 출금 오픈 시점. 모르면 '미정'."),
    "document": Field("개정 대상", "text", "예) 빗썸 이용약관, 개인정보처리방침", exact=True),
    "effective_at": Field("시행일", "date", "개정 시행(적용)일. 예) 2026-09-30"),
    "changes": Field("주요 개정 내용", "text", "무엇이 어떻게 바뀌는지 알려주세요."),
    "service": Field("대상 서비스", "text", "예) 운전면허증 진위확인 서비스", exact=True),
    "restored_at": Field("정상화 시점", "datetime", "정상화된 일시(정상화 공지일 때). 예) 2026-09-10 16:30"),
    "targets": Field("제한 대상 거래소·기관", "text", "예) Opal Exchange, Sadaf Exchange", exact=True),
    "restricted_at": Field("제한일", "date", "제한 적용일. 예) 2026-08-31"),
    "law_clause": Field("법령·규정 조항", "text", "인용할 조항이 확정됐으면 알려주세요. 없으면 [조항 확인 필요]로 남깁니다."),
    "links": Field("참고 링크", "urls", "공지에 넣을 링크가 있으면 알려주세요."),
    "topic": Field("공지 주제", "text", "어떤 내용의 공지인가요? 한 줄로."),
    "details": Field("핵심 내용", "text", "공지에 꼭 들어가야 할 내용을 알려주세요."),
}


# ── 유형 ────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class NoticeType:
    category: str
    subtype: str
    label: str
    pattern: str = ""                   # 제목/요청문 라우팅 정규식(general은 빈 값)
    required: tuple[str, ...] = ()
    optional: tuple[str, ...] = ()
    query: str = ""                     # 검색어 씨앗(실제 제목 표현)
    title_boost: tuple[str, ...] = ()   # 제목에 이 구절이 있으면 검색 가점
    title_template: str = ""            # 권장 제목. {coins} 등 필드명 치환
    sections: tuple[tuple[str, str], ...] = ()  # (항목명, 존재 확인 정규식) — 없으면 경고
    labels: tuple[tuple[str, str], ...] = ()    # (라벨 정규식, 필드) — 라벨 줄 값과 입력 대조
    guards: tuple[tuple[str, str], ...] = ()    # (필드, 정규식) — 필드 미입력인데 초안에 있으면 오류
    roles: tuple[tuple[str, str], ...] = ()     # (정규식 (?P<ticker>…), 필드) — 이 자리의 가상자산은 그 필드 값이어야 함
    field_labels: tuple[tuple[str, str], ...] = ()  # 이 유형에서만 쓰는 필드 이름(프롬프트·질문·오류 메시지)
    # 제목만으로 갈리지 않는 형제 유형(같은 family)은 과거 공지 본문으로 가른다.
    # 예) 에어드랍 '지급 완료 안내'(본문 '지급이 완료되었습니다') vs '지원·지급 예정 안내'(본문 '…예정입니다')
    family: str = ""
    body_pattern: str = ""

    @property
    def key(self) -> str:
        return f"{self.category}/{self.subtype}"

    @property
    def fields(self) -> tuple[str, ...]:
        return self.required + self.optional


def _general(category: str) -> NoticeType:
    return NoticeType(
        category, "general", f"{category} 일반",
        required=("topic", "details"),
        optional=("coins", "reason", "links", "law_clause"),
    )


_DW_SECTIONS = (("대상 가상자산", r"대상\s*가상자산"), ("유의사항", r"유의"))

# 에어드랍 공통: 지급 코인과 보유 기준 코인이 뒤바뀌던 문제('가스(GAS) 보유자 대상 가스(GAS) 에어드랍')
# 줄을 넘지 않는다([ \t]*): '…10:00(KST)' 다음 줄의 '지급 수량'을 한 덩어리로 읽어 KST를 코인으로 본 오탐이 있었다
_AIRDROP_ROLES = ((r"\((?P<ticker>[A-Z0-9]{2,12})\)[ \t]*보유(?:자|[ \t]*회원|[ \t]*비율|[ \t]*수량)", "holding_coins"),
                  (r"\((?P<ticker>[A-Z0-9]{2,12})\)[ \t]*(?:에어드[랍롭]|지급[ \t]*수량|배당[ \t]*수량)", "coins"))
# '대상 가상자산'은 공지 속 '지급 대상: … 보유 회원'과 헷갈려 역할이 통째로 뒤바뀐 초안이 나왔다
_AIRDROP_LABELS = (("coins", "지급 가상자산(에어드랍으로 나눠 주는 코인)"),
                   ("holding_coins", "보유 기준 가상자산(이 코인을 보유한 회원이 받음)"))
_AIRDROP_DONE = r"지급이\s*완료|지급\s*완료|지급되었습니다|지급하였습니다|지급을\s*완료"
_AIRDROP_PLAN = r"(지급|지원|진행|스냅샷|반영)[^.\n]{0,15}예정|예정(입니다|이오니|이며)"

# 카테고리 안에서는 위에서부터 먼저 맞는 규칙이 이긴다(예: '지정 해제'는 '지정'보다 먼저).
TYPES: tuple[NoticeType, ...] = (
    # 입출금 1,245건: 중지/중단 1,099 · 지연/정상화 87 · 기타 59
    NoticeType(
        "입출금", "delay", "입출금 일시 지연",
        pattern=r"지연|정상화|적체",
        required=("coins", "reason"),
        optional=("network", "occurred_at", "status", "restored_at"),
        query="입출금 일시 지연 안내", title_boost=("지연",),
        title_template="{coins} 입출금 일시 지연 안내",
        sections=(("대상 가상자산", r"대상\s*가상자산"), ("지연 사유", r"사유")),
        labels=((r"대상\s*네트워크", "network"),),
    ),
    NoticeType(
        "입출금", "suspend", "입출금 일시 중지",
        pattern=r"중지|중단|업그레이드|하드포크|메인넷|네트워크\s*전환|마이그레이션",
        required=("coins", "suspend_at", "reason"),
        optional=("network", "resume_at", "scope", "upgrade_at", "law_clause", "links"),
        query="입출금 일시 중지 안내", title_boost=("입출금 일시 중지",),
        title_template="{coins} 입출금 일시 중지 안내",
        sections=_DW_SECTIONS + (("중지 시점", r"(중지|중단)\s*시점"), ("재개 시점", r"재개")),
        labels=((r"대상\s*네트워크", "network"), (r"(중지|중단)\s*시점", "suspend_at"),
                (r"재개\s*시점", "resume_at")),
        # 업데이트된 참고 공지의 재개 안내('…입출금 서비스를 재개합니다')가 새 공지에 섞이는 것 방지
        guards=(("resume_at", r"재개(?:합니다|하였습니다|되었습니다|했습니다)"),),
    ),
    # 거래유의 287건: 지정 157 · 연장 64 · 해제 64
    NoticeType(
        "거래유의", "release", "거래유의종목 지정 해제",
        pattern=r"해제|해지",
        required=("coins", "released_at"),
        optional=("reason", "deposit_resume_at"),
        query="거래유의종목 지정 해제", title_boost=("지정 해제",),
        title_template="{coins} 거래유의종목 지정 해제",
        sections=(("해제 종목·사유", r"해제"), ("해제 일정", r"해제일|해제\s*일정")),
        labels=((r"해제일", "released_at"), (r"입금\s*재개", "deposit_resume_at")),
    ),
    NoticeType(
        "거래유의", "extend", "거래유의종목 지정 연장",
        pattern=r"연장",
        required=("coins", "reason", "next_review"),
        optional=("law_clause",),
        query="거래유의종목 지정 연장", title_boost=("지정 연장",),
        title_template="{coins} 거래유의종목 지정 연장",
        sections=(("연장 종목·사유", r"연장"), ("연장 일정", r"일정")),
    ),
    NoticeType(
        "거래유의", "designate", "거래유의종목 지정",
        pattern=r"지정",
        required=("coins", "reason", "designated_at"),
        optional=("deposit_status", "next_review", "law_clause"),
        query="거래유의종목 지정", title_boost=("거래유의종목 지정",),
        title_template="{coins} 거래유의종목 지정",
        sections=(("지정 사유", r"사유"), ("지정 일정", r"지정일")),
        labels=((r"지정일", "designated_at"), (r"공지\s*일정", "next_review")),
        # 참고 공지의 '현재 OO의 입출금이 중단된 상태입니다'가 입력 없이 옮겨지던 문제
        guards=(("deposit_status", r"(입금|출금|입출금)[^\n.]{0,15}(중단|중지|제한)된\s*상태"),
                ("deposit_status", r"입금\s*(중지|중단)\s*시점\s*:")),
    ),
    # 공시 8건: 매도 계획 3 · 매도 결과 3 · 재산상 이익 제공 2(본문 없음 → general)
    NoticeType(
        "공시", "sale_plan", "보유 가상자산 매도 계획 공시",
        pattern=r"매도\s*계획",
        required=("seller",),
        optional=("details", "links", "law_clause"),
        query="보유 가상자산 매도 계획 공시", title_boost=("매도 계획",),
        title_template="{seller} 보유 가상자산 매도 계획 공시",
        sections=(("매도계획", r"매도\s*계획"),),
    ),
    NoticeType(
        "공시", "sale_result", "보유 가상자산 매도 결과 공시",
        pattern=r"매도\s*결과",
        required=("seller",),
        optional=("details", "links", "law_clause"),
        query="보유 가상자산 매도 결과 공시", title_boost=("매도 결과",),
        title_template="{seller} 보유 가상자산 매도 결과 공시",
        sections=(("매도결과", r"매도\s*결과"),),
    ),
    # 안내 944건: 에어드랍 279 · 서비스 지연/중단 72 · 약관/방침 개정 45 · 유의촉구 36
    #             · 해외 거래소 입출금 거래 제한 13(전부 2025+) · 그 외 499(general)
    NoticeType(
        "안내", "caution_urge", "유의촉구",
        pattern=r"유의\s*촉구",
        required=("coins", "reason"),
        optional=("links", "deposit_status"),
        query="유의촉구 안내", title_boost=("유의촉구",),
        title_template="{coins} 유의촉구 안내",
        sections=(("투자 유의 문구", r"유의"),),
    ),
    # 에어드랍 279건은 두 부류: 지급을 마치고 올리는 '지급 완료 안내' 159건(2025+ 113, 138건은 날짜 없음)과
    # 앞으로의 계획을 알리는 '지원·지급 예정 안내' 114건(77건은 스냅샷·지급 일시 있음). 제목만으론 안 갈려
    # ('…에어드랍 4회차 지급 안내'인데 본문은 예정) 과거 공지는 본문 표현으로 가른다(family/body_pattern).
    NoticeType(
        "안내", "airdrop_plan", "에어드랍 지원·지급 예정",
        pattern=r"에어드[랍롭][^\n]*(지원|예정)|(지원|예정)[^\n]*에어드[랍롭]",
        required=("coins", "holding_coins", "snapshot"),
        optional=("paid_at", "withdraw_open", "round", "details"),
        query="보유 회원 대상 에어드랍 지원 안내", title_boost=("에어드랍 지원",),
        title_template="{holding_coins} 보유 회원 대상 {coins} 에어드랍 지원 안내",
        sections=(("에어드랍 대상", r"대상"), ("스냅샷 시점", r"스냅샷"), ("유의사항", r"유의")),
        roles=_AIRDROP_ROLES,
        field_labels=_AIRDROP_LABELS + (("paid_at", "지급 예정일"),),
        family="airdrop", body_pattern=_AIRDROP_PLAN,
    ),
    NoticeType(
        "안내", "airdrop_paid", "에어드랍 지급 완료",
        pattern=r"에어드[랍롭]",
        required=("coins", "holding_coins", "snapshot"),
        optional=("round", "details"),   # 지급을 마친 뒤 올리는 안내라 지급일은 받지도 요구하지도 않는다
        query="에어드랍 지급 안내", title_boost=("에어드랍",),
        title_template="{coins} 에어드랍 지급 안내",
        sections=(("지급 대상·기준", r"지급\s*(대상|기준|조건)|스냅샷"), ("유의사항", r"유의")),
        roles=_AIRDROP_ROLES,
        field_labels=_AIRDROP_LABELS,
        family="airdrop", body_pattern=_AIRDROP_DONE,
    ),
    NoticeType(
        "안내", "policy_revision", "약관·방침 개정",
        pattern=r"(약관|처리방침)[^\n]*개정|개정[^\n]*(약관|처리방침)",
        required=("document", "effective_at", "changes"),
        optional=("reason",),
        query="개정 안내", title_boost=("개정",),
        title_template="{document} 개정 안내",
        sections=(("개정 내용", r"개정"), ("시행일", r"시행|적용")),
    ),
    NoticeType(
        "안내", "exchange_restriction", "해외 거래소 입출금 거래 제한",
        pattern=r"(해외|거래소|플랫폼|기관)[^\n]*거래\s*제한",
        required=("targets", "reason", "restricted_at"),
        optional=("law_clause",),
        query="해외 가상자산거래소 입출금 거래 제한 안내", title_boost=("입출금 거래 제한",),
        title_template="해외 가상자산거래소({targets}) 입출금 거래 제한 안내",
        sections=(("제한 대상", r"거래\s*차단|제한\s*대상|대상"), ("일시", r"일시")),
    ),
    NoticeType(
        "안내", "service_incident", "서비스 일시 지연·중단",
        pattern=r"지연|정상화|일시\s*(중단|불가)|순단|장애",
        required=("service", "status"),
        optional=("reason", "occurred_at", "restored_at"),
        query="서비스 일시 지연 안내", title_boost=("일시 지연",),
        title_template="{service} 일시 지연 안내",
    ),
)


def field_label(ntype: NoticeType, name: str) -> str:
    """유형별 이름이 있으면 그것, 없으면 공통 이름."""
    return dict(ntype.field_labels).get(name, FIELDS[name].label)


def known_categories() -> tuple[str, ...]:
    if _CATEGORIES_PATH.exists():
        return tuple(json.loads(_CATEGORIES_PATH.read_text(encoding="utf-8")))
    return tuple(dict.fromkeys(t.category for t in TYPES))


def types_for(category: str) -> list[NoticeType]:
    """카테고리의 유형 목록(라우팅 순서) + 마지막에 general."""
    return [t for t in TYPES if t.category == category] + [_general(category)]


def get_type(category: str, subtype: str) -> NoticeType | None:
    return next((t for t in types_for(category) if t.subtype == subtype), None)


def route(category: str, text: str, body: str | None = None) -> tuple[NoticeType, str]:
    """요청문/제목으로 subtype 판별. (유형, 매칭된 표현). 안 맞으면 general.

    body(과거 공지 본문)를 주면, 본문 규칙이 있는 유형은 본문도 맞아야 한다. 제목은 맞는데 본문이
    다르면 같은 family 중 본문이 맞는 형제 유형으로 보낸다(예: '…에어드랍 4회차 지급 안내' + 본문 '예정').
    """
    types = types_for(category)
    for t in types:
        m = re.search(t.pattern, text or "") if t.pattern else None
        if not m:
            continue
        if body is not None and t.body_pattern and not re.search(t.body_pattern, body):
            sib = next((s for s in types if s is not t and s.family and s.family == t.family
                        and s.body_pattern and re.search(s.body_pattern, body)), None)
            if sib:
                return sib, m.group(0)
            continue
        return t, m.group(0)
    return _general(category), ""


def classify_title(title: str, categories: list[str], body: str | None = None) -> dict[str, str]:
    """과거 공지 제목(+선택: 본문) → {카테고리: subtype}. 후보 공지의 유형 판별용(같은 규칙 재사용)."""
    return {c: route(c, title, body)[0].subtype for c in categories}


# ── 값 정규화 ───────────────────────────────────────────────────────────
_COIN_STR_RE = re.compile(r"^\s*(.+?)\s*\(\s*([A-Za-z0-9]{1,12})\s*\)\s*$")
_TICKER_RE = re.compile(r"^[A-Z0-9]{1,12}$")
_DT_RE = re.compile(
    r"^\s*(\d{4})\s*[-./년]\s*(\d{1,2})\s*[-./월]\s*(\d{1,2})\s*일?"
    r"(?:\s*\([월화수목금토일]\))?(?:[\sT]+(\d{1,2})\s*[:시]\s*(\d{2})?\s*분?(?::\d{2})?)?\s*$"
)


def is_unknown(value) -> bool:
    return isinstance(value, str) and value.strip().lower() in UNKNOWN_VALUES


def is_empty(value) -> bool:
    return value is None or (isinstance(value, (str, list, tuple, dict)) and not value)


def parse_datetime(value) -> tuple[datetime, bool] | None:
    """'2026-09-10 19:00' / '2026.09.10' 등 → (datetime, 시각 포함 여부). 실패 시 None."""
    m = _DT_RE.match(str(value or ""))
    if not m:
        return None
    y, mo, d, hh, mm = m.groups()
    try:
        dt = datetime(int(y), int(mo), int(d), int(hh or 0), int(mm or 0))
    except ValueError:
        return None
    return dt, hh is not None


def render_datetime(value, date_only: bool = False) -> str:
    """공지 표기로 렌더. 요일은 코드로 계산한다(LLM이 요일을 틀리는 문제 방지).
    '2026-09-10 19:00' → '2026.09.10(목) 오후 7:00(KST)', '2026-09-10' → '2026.09.10(목)'."""
    parsed = parse_datetime(value)
    if not parsed:
        return str(value)
    dt, has_time = parsed
    out = f"{dt:%Y.%m.%d}({_WEEKDAYS[dt.weekday()]})"
    if has_time and not date_only:
        ampm = "오전" if dt.hour < 12 else "오후"
        h12 = dt.hour % 12 or 12
        out += f" {ampm} {h12}:{dt.minute:02d}(KST)"
    return out


def _coin(item) -> dict | None:
    if isinstance(item, dict):
        name = str(item.get("name") or item.get("coin_kr") or "").strip()
        ticker = str(item.get("ticker") or "").strip().upper()
        return {"name": name, "ticker": ticker} if (name or ticker) else None
    s = str(item or "").strip()
    if not s:
        return None
    m = _COIN_STR_RE.match(s)
    if m:
        return {"name": m.group(1).strip(), "ticker": m.group(2).upper()}
    if _TICKER_RE.match(s.upper()) and s.isascii():
        return {"name": "", "ticker": s.upper()}
    return {"name": s, "ticker": ""}


def normalize_coins(value) -> list[dict]:
    """list[dict] / list[str] / '메가이더(MEGA), 비너스(XVS)' → [{'name','ticker'}]."""
    if is_empty(value):
        return []
    items = value if isinstance(value, (list, tuple)) else re.split(r"\s*[,/]\s*", str(value))
    return [c for c in (_coin(i) for i in items) if c]


def coin_label(coins: list[dict]) -> str:
    return ", ".join(f"{c['name']}({c['ticker']})" if c["name"] and c["ticker"]
                     else (c["name"] or c["ticker"]) for c in coins)


def normalize_inputs(raw: dict | None, ntype: NoticeType) -> dict:
    """문답값 정리. 예전 키(coin_kr/ticker/datetime/action)도 받아준다."""
    raw = dict(raw or {})
    out: dict = {}
    for k, v in raw.items():
        if isinstance(v, str):
            v = v.strip()
        if not is_empty(v):
            out[k] = v
    # (구) coin_kr + ticker → coins
    if "coins" not in out and (out.get("coin_kr") or out.get("ticker")):
        out["coins"] = [{"name": out.get("coin_kr", ""), "ticker": out.get("ticker", "")}]
    for k in ("coin_kr", "coin_en", "ticker"):
        out.pop(k, None)
    for name in list(out):
        kind = FIELDS[name].kind if name in FIELDS else "text"
        if kind == "coins":
            out[name] = normalize_coins(out[name])
        elif kind == "urls" and isinstance(out[name], str):
            out[name] = [u for u in re.split(r"[\s,]+", out[name]) if u]
    # (구) datetime → 이 유형의 대표 일시 필드
    if "datetime" in out:
        dt_fields = [f for f in ntype.fields if is_temporal(FIELDS[f].kind)]
        if dt_fields and dt_fields[0] not in out:
            out[dt_fields[0]] = out["datetime"]
        out.pop("datetime")
    # (구) action → topic (general 유형)
    if "action" in out and "topic" in ntype.fields and "topic" not in out:
        out["topic"] = out["action"]
    if "details" in ntype.fields and "details" not in out and out.get("reason"):
        out["details"] = out["reason"]
    return out


def _invalid(name: str, value) -> str | None:
    kind = FIELDS[name].kind
    if is_unknown(value):
        return "가상자산은 '미정'일 수 없습니다." if kind in ("coins", "urls") else None
    if is_temporal(kind) and not parse_datetime(value):
        return "일시 형식을 알 수 없습니다. 예) 2026-09-10 19:00 또는 2026-09-10"
    if kind == "coins":
        for c in value:
            if not c["name"] or not c["ticker"]:
                return "가상자산은 '한글명(티커)' 형태로 알려주세요. 예) 메가이더(MEGA)"
            if not _TICKER_RE.match(c["ticker"]):
                return f"티커 형식이 올바르지 않습니다: {c['ticker']}"
    if kind == "urls" and any(not str(u).startswith(("http://", "https://")) for u in value):
        return "링크는 http(s):// 로 시작해야 합니다."
    return None


# ── 파트 해석(카테고리 1~2개) ─────────────────────────────────────────────
@dataclass
class Part:
    ntype: NoticeType
    inputs: dict
    matched: str = ""          # 라우팅 근거(매칭된 표현)
    overridden: bool = False   # 사용자가 subtype을 직접 지정
    estimated: bool = False    # 규칙으로 못 정해 비슷한 공지로 추정(사용자 확인 필요)

    @property
    def category(self) -> str:
        return self.ntype.category

    def to_dict(self) -> dict:
        return {"category": self.category, "subtype": self.ntype.subtype, "label": self.ntype.label,
                "matched": self.matched, "overridden": self.overridden, "estimated": self.estimated}


@dataclass
class Resolution:
    parts: list[Part] = field(default_factory=list)
    missing: list[dict] = field(default_factory=list)
    invalid: list[dict] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)      # 구조 오류(카테고리 개수 등)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not (self.errors or self.missing or self.invalid)


def routing_text(text: str, inputs: dict | None) -> str:
    """유형 판별에 쓰는 문장 = 요청문 + (구)action + 사유."""
    inputs = inputs or {}
    return " ".join(str(x) for x in (text, inputs.get("action", ""), inputs.get("reason", "")) if x)


def resolve(
    categories: list[str],
    *,
    text: str = "",
    inputs: dict | None = None,
    part_inputs: dict[str, dict] | None = None,
    subtypes: dict[str, str] | None = None,
    estimated: dict[str, str] | None = None,
) -> Resolution:
    """카테고리(1~2개) + 요청문 + 문답값 → 파트별 유형·입력 + 누락/오류 목록.

    inputs 는 파트 공통 값, part_inputs[카테고리] 는 그 파트에만 적용되는 값(덮어쓰기).
    예) 거래유의+거래지원종료에서 파트마다 대상 코인이 다를 때.
    estimated[카테고리]: 규칙 판별이 general일 때만 쓰는 추정 유형(drafting.estimate_subtypes).
    사용자가 subtypes로 지정한 값이 항상 우선한다.
    """
    res = Resolution()
    cats = [c.strip() for c in (categories or []) if c and c.strip()]
    if not 1 <= len(cats) <= MAX_CATEGORIES:
        res.errors.append(f"카테고리는 1~{MAX_CATEGORIES}개를 선택해야 합니다(현재 {len(cats)}개).")
        return res
    if len(set(cats)) != len(cats):
        res.errors.append("같은 카테고리를 두 번 선택할 수 없습니다.")
        return res
    known = known_categories()
    for c in cats:
        if c not in known:
            res.errors.append(f"알 수 없는 카테고리: {c}. 가능: {', '.join(known)}")
    if res.errors:
        return res

    inputs = inputs or {}
    part_inputs = part_inputs or {}
    subtypes = subtypes or {}
    estimated = estimated or {}
    route_text = routing_text(text, inputs)

    for c in cats:
        if c not in TARGET_CATEGORIES:
            res.warnings.append(f"'{c}'은(는) 이번 구현 범위 밖이라 일반 규칙(general)으로 처리합니다.")
        wanted = subtypes.get(c)
        if wanted:
            ntype = get_type(c, wanted)
            if ntype is None:
                res.errors.append(f"'{c}'에 없는 subtype: {wanted}. 가능: "
                                  + ", ".join(t.subtype for t in types_for(c)))
                continue
            part = Part(ntype, {}, overridden=True)
        else:
            ntype, matched = route(c, route_text)
            guess = get_type(c, estimated[c]) if c in estimated else None
            if ntype.subtype == "general" and guess is not None:
                part = Part(guess, {}, matched="비슷한 공지로 추정", estimated=True)
            else:
                part = Part(ntype, {}, matched=matched)
        part.inputs = normalize_inputs({**inputs, **part_inputs.get(c, {})}, part.ntype)
        if "topic" in part.ntype.fields and is_empty(part.inputs.get("topic")) and text.strip():
            part.inputs["topic"] = text.strip()   # general은 요청문을 공지 주제로 쓴다
        res.parts.append(part)
    if res.errors:
        return res

    seen: dict[str, dict] = {}
    for p in res.parts:
        for name in p.ntype.required:
            if is_empty(p.inputs.get(name)):
                entry = seen.get(name)
                if entry:   # 두 파트 모두 없으면 한 번만 묻는다(공통 값으로 채우면 됨)
                    entry["categories"].append(p.category)
                    continue
                f = FIELDS[name]
                entry = {"field": name, "label": field_label(p.ntype, name), "question": f.question,
                         "categories": [p.category]}
                seen[name] = entry
                res.missing.append(entry)
        for name in p.ntype.fields:
            v = p.inputs.get(name)
            if is_empty(v):
                continue
            problem = _invalid(name, v)
            if problem and not any(i["field"] == name for i in res.invalid):
                res.invalid.append({"field": name, "label": FIELDS[name].label,
                                    "value": v, "problem": problem, "category": p.category})
    return res


# ── 프롬프트/검증용 표현 ─────────────────────────────────────────────────
def display_value(name: str, value) -> str:
    """입력값을 공지 표기로. 일시는 요일 포함 하우스 포맷."""
    kind = FIELDS[name].kind if name in FIELDS else "text"
    if is_unknown(value):
        return "미정(본문에는 확정 값을 쓰지 말고 '추후 안내' 또는 [확인 필요]로 표기)"
    if kind == "coins":
        return coin_label(value)
    if is_temporal(kind):
        return render_datetime(value, date_only=kind == "date")
    if isinstance(value, (list, tuple)):
        return ", ".join(str(v) for v in value)
    return str(value)


def title_hint(parts: list[Part]) -> str:
    """스펙 템플릿으로 권장 제목. 값이 모자라면 빈 문자열(억지 제목 금지)."""
    titles: list[tuple[str, str]] = []
    for p in parts:
        tpl = p.ntype.title_template
        if not tpl:
            return ""
        values = {}
        for name in re.findall(r"{(\w+)}", tpl):
            v = p.inputs.get(name)
            if is_empty(v) or is_unknown(v):
                return ""
            values[name] = display_value(name, v)
        titles.append((tpl.format(**values), values.get("coins", "")))
    if len(titles) == 1:
        return titles[0][0]
    (t1, c1), (t2, c2) = titles
    head = t1.removesuffix(" 안내")
    if c1 and c1 == c2 and t2.startswith(c2):
        return f"{head} 및 {t2[len(c2):].strip()}"
    return f"{head} 및 {t2}"


def catalog() -> list[dict]:
    """프론트 문답용 유형 카탈로그(GET /types)."""
    out = []
    for c in TARGET_CATEGORIES:
        subs = []
        for t in types_for(c):
            subs.append({
                "subtype": t.subtype, "label": t.label,
                "required": [{"field": n, **asdict(FIELDS[n]), "label": field_label(t, n)} for n in t.required],
                "optional": [{"field": n, **asdict(FIELDS[n]), "label": field_label(t, n)} for n in t.optional],
            })
        out.append({"category": c, "subtypes": subs})
    return out
