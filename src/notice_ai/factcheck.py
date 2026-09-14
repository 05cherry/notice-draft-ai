"""초안 사실 검증(결정적) + 참고 공지 마스킹. 외부 호출 없는 순수 함수만.

원칙: 과거 공지는 형식·문체의 예시일 뿐 새 공지의 사실 출처가 아니다.
  - 생성 전: 참고 공지의 사실값(조항 번호·링크·가상자산·날짜·시각)을 가린다 → mask_reference
  - 생성 후: 초안의 사실값이 이번 입력에서 왔는지 코드로 대조한다     → check_draft
error는 수정(revision) 대상, warn은 사람에게 보여줄 확인 사항이다.
"""

from __future__ import annotations

import difflib
import re
from dataclasses import asdict, dataclass, field
from datetime import date

from notice_ai.notice_types import (
    FIELDS,
    Part,
    coin_label,
    display_value,
    field_label,
    is_empty,
    is_temporal,
    is_unknown,
    parse_datetime,
    render_datetime,
)

MASK_COIN, MASK_TICKER = "<가상자산명>", "<티커>"
MASK_DATE, MASK_TIME, MASK_URL, MASK_ROUND = "<날짜>", "<시각>", "<링크>", "<차수>"
MASK_CLAUSE = "[조항 확인 필요]"   # 결정 (a): 과거 공지의 조항 번호는 프롬프트에 노출하지 않는다

_WEEKDAYS = "월화수목금토일"
_URL_RE = re.compile(r"https?://[^\s)\]}>\"'<가-힣]+")
# 법령 조항 패턴: 조/호/법령명만 잡는다. '목'은 제외(제목·항목 등 오탐 방지).
_LAW_RE = re.compile(r"제\s*\d+\s*조|제\s*\d+\s*호|가상자산이용자보호법|시행령")
# 조항 인용 전체(예: '제17조 제1호 마목')
_CLAUSE_RE = re.compile(r"제\s*\d+\s*조(?:\s*제\s*\d+\s*[항호])*(?:\s*[가-힣]\s*목)?")
_WD = r"(?:\s*\(\s*([월화수목금토일])\s*\))?"
_FULL_DATE_RE = re.compile(r"(?<!\d)(\d{4})\s*[.\-/]\s*(\d{1,2})\s*[.\-/]\s*(\d{1,2})(?!\d)" + _WD)
_KOR_DATE_RE = re.compile(r"(?:(\d{4})\s*년\s*)?(?<!\d)(\d{1,2})\s*월\s*(\d{1,2})\s*일" + _WD)
_MD_RE = re.compile(r"(?<![\d./])(\d{1,2})\s*/\s*(\d{1,2})(?![\d/])" + _WD)   # 제목 '(09/10 재개)', '9/14 ~ 9/18'
_SHORT_DATE_RE = re.compile(r"(?<![\d.])(\d{2})\.\s*(\d{1,2})\.\s*(\d{1,2})\." + _WD)  # '26. 08. 27.'(심사필 기간)
_YM_RE = re.compile(r"(?<!\d)\d{4}\s*년\s*\d{1,2}\s*월")                        # '2025년 11월'(마스킹 전용)
_APPROVAL_RE = re.compile(r"심사필\s*제\s*\d+\s*호")                             # 준법감시인 심사필 번호
_ROUND_RE = re.compile(r"(?<!\d)\d{1,2}\s*월\s*\d\s*주차|(?<![\d제])\d{1,3}\s*(?:회차|차)(?![가-힣])")  # '9월 1주차', '17차'
_AMPM_RE = re.compile(r"(오전|오후)\s*(\d{1,2})(?:\s*:\s*(\d{2})|\s*시(?:\s*(\d{1,2})\s*분)?)?")
_CLOCK_RE = re.compile(r"(?<![\d:])(\d{1,2}):(\d{2})(?::\d{2})?(?![\d:])")
_HOUR_RE = re.compile(r"(?<![\d:])(\d{1,2})\s*시(?![간점작스장세청총행설기즌])(?:\s*(\d{1,2})\s*분)?")
_PAREN_TICKER_RE = re.compile(r"\(\s*([A-Z0-9]{2,12})\s*\)")
_TITLE_RESUME_RE = re.compile(r"\(\s*(\d{1,2})\s*/\s*(\d{1,2})[^)]*재개")
_PLACEHOLDER_RE = re.compile(r"<[^<>\n]{1,15}>|○{2,}|O{3,}|X{3,}|\{[^{}\n]{1,20}\}")
_CONFIRM_MARK_RE = re.compile(r"\[[^\]\n]*확인\s*필요[^\]\n]*\]")
# 괄호 속 대문자지만 가상자산 티커가 아닌 것
_NON_COIN = {"KST", "UTC", "KRW", "DAXA", "OFAC", "VASP", "CODE", "API", "APP", "KYC", "AML",
             "IPO", "FAQ", "OTP", "PC", "KCSI", "DMCC", "FIU", "ISMS", "ISO", "ID", "2FA", "NFT",
             "SMS", "ARS", "URL", "PDF"}


# ── 추출기 ──────────────────────────────────────────────────────────────
def _has_letter(s: str) -> bool:
    return any(ch.isalpha() for ch in s)


def paren_tickers(text: str) -> set[str]:
    """'메가이더(MEGA)' 꼴의 티커. (KST) 같은 비코인 약어·숫자만인 것은 제외."""
    return {t for t in _PAREN_TICKER_RE.findall(text or "") if t not in _NON_COIN and _has_letter(t)}


def extract_dates(text: str) -> list[tuple[int | None, int, int, str | None, str]]:
    """(연 또는 None, 월, 일, 요일 또는 None, 원문). 링크 속 숫자는 무시."""
    text = _URL_RE.sub(" ", text or "")
    out, spans = [], []
    for rx, kind in ((_FULL_DATE_RE, "full"), (_SHORT_DATE_RE, "short"), (_KOR_DATE_RE, "kor"), (_MD_RE, "md")):
        for m in rx.finditer(text):
            if any(s <= m.start() < e for s, e in spans):
                continue
            g = m.groups()
            if kind == "md":
                y, mo, d, wd = None, int(g[0]), int(g[1]), g[2]
            elif kind == "short":
                y, mo, d, wd = 2000 + int(g[0]), int(g[1]), int(g[2]), g[3]
            else:
                y, mo, d, wd = (int(g[0]) if g[0] else None), int(g[1]), int(g[2]), g[3]
            if not (1 <= mo <= 12 and 1 <= d <= 31):
                continue
            spans.append(m.span())
            out.append((y, mo, d, wd, m.group(0).strip()))
    return out


def extract_times(text: str) -> list[tuple[int, int, str]]:
    """(시 24h, 분, 원문). '오후 3:00' → 15:00, '16시' → 16:00."""
    text = _URL_RE.sub(" ", text or "")
    out = []
    for m in _AMPM_RE.finditer(text):
        hh = int(m.group(2))
        if m.group(1) == "오후" and hh != 12:
            hh += 12
        elif m.group(1) == "오전" and hh == 12:
            hh = 0
        out.append((hh, int(m.group(3) or m.group(4) or 0), m.group(0)))
    text = _AMPM_RE.sub(" ", text)
    for m in _CLOCK_RE.finditer(text):
        out.append((int(m.group(1)), int(m.group(2)), m.group(0)))
    text = _CLOCK_RE.sub(" ", text)
    for m in _HOUR_RE.finditer(text):
        out.append((int(m.group(1)), int(m.group(2) or 0), m.group(0)))
    return [t for t in out if 0 <= t[0] <= 24 and 0 <= t[1] <= 59]


def clauses(text: str) -> set[str]:
    """조항 인용을 공백 없는 형태로."""
    return {re.sub(r"\s+", "", m) for m in _CLAUSE_RE.findall(text or "")}


# ── 참고 공지: 업데이트 전(최초) 버전 복원 ───────────────────────────────────
# 빗썸은 재개·연기·정상화 등이 생기면 같은 공지를 고친다: 제목 끝에 '(09/12 재개)'를 붙이고,
# 본문 맨 위에 <hr>로 구분한 안내를 덧붙인다(수집 시 태그가 지워져 raw_text에는 구분선이 없다).
# 실측(4개 카테고리 2,439건): 업데이트 안내는 항상 원문 '위'에 쌓이고, 원문은 마지막 '안녕하세요'부터다.
#   - 재개 블록이 인사 없이 붙은 경우 1,041건 / 재개 블록도 '안녕하세요'로 시작 27건 / 여러 번 쌓임(USDT 등)
#   - 제목만 바뀐 경우 147건(원문 아래에 덧붙인 사례 0건)
# 끝 괄호에 재개·연기·완료 등이 있거나, 날짜와 함께 지급·출금이 있으면 업데이트 꼬리표
# (예: 에어드랍 지원 안내가 지급 뒤 '(11/07 지급)'으로 바뀜. '(2차 스냅샷 안내)'처럼 원래 제목인 괄호는 둔다)
_UPDATE_TAG_RE = re.compile(
    r"\s*\((?=[^()]*(?:재개|연기|완료|정상화|종료|업데이트|취소|변경|추가)"
    r"|[^()]*\d{1,2}\s*/\s*\d{1,2}[^()]*(?:지급|출금))[^()]*\)\s*$")
_GREETING_RE = re.compile(r"안녕하세요")
_INVISIBLE_RE = re.compile(r"[\ufeff\u200b\u200c\u200d]")   # BOM·폭 없는 공백(일부 옛 공지 앞에 붙어 있음)
MIN_ORIGINAL = 100     # 잘라낸 원문이 이보다 짧으면 판단이 불확실하므로 자르지 않는다


def original_version(title: str, body: str) -> tuple[str, str, list[str]]:
    """업데이트된 공지에서 최초 버전(제목·본문)을 복원한다. (제목, 본문, 제거 내역)."""
    notes: list[str] = []
    t = title or ""
    while m := _UPDATE_TAG_RE.search(t):
        notes.append(f"제목 꼬리표 '{m.group(0).strip()}' 제거")
        t = t[:m.start()].rstrip()
    b = _INVISIBLE_RE.sub("", body or "")
    starts = [m.start() for m in _GREETING_RE.finditer(b)]
    if starts:
        head, orig = b[:starts[-1]].strip(), b[starts[-1]:]
        if head and len(orig.strip()) >= MIN_ORIGINAL:
            notes.append(f"본문 위에 덧붙은 업데이트 안내 {len(head)}자 제외")
            b = orig
    return t, b, notes


# ── 참고 공지 마스킹 ─────────────────────────────────────────────────────
def _premask(text: str) -> str:
    """코인 외 사실값(링크·심사필 번호·조항·날짜·차수·시각)을 먼저 가린다."""
    t = _URL_RE.sub(MASK_URL, text or "")
    t = _APPROVAL_RE.sub("심사필 [번호 확인 필요]", t)
    t = _CLAUSE_RE.sub(MASK_CLAUSE, t)
    for rx in (_FULL_DATE_RE, _SHORT_DATE_RE, _KOR_DATE_RE, _YM_RE, _MD_RE):
        t = rx.sub(MASK_DATE, t)
    t = _ROUND_RE.sub(MASK_ROUND, t)
    for rx in (_AMPM_RE, _CLOCK_RE, _HOUR_RE):
        t = rx.sub(MASK_TIME, t)
    return t


def _coin_masks(premasked: str, tickers) -> dict[str, tuple[str, str]]:
    """티커 → (<가상자산명N>, <티커N>). 여러 개면 등장 순서대로 번호를 붙여 역할을 보존한다.
    예) 에어드랍 '<가상자산명2>(<티커2>) 보유자 대상 <가상자산명1>(<티커1>) 지급' — 번호 없이 모두
    <티커>로 가리면 지급 코인과 보유 기준 코인이 구분되지 않아 계산식의 코인이 뒤바뀌었다."""
    def first_pos(tk: str) -> int:
        m = re.search(r"(?<![A-Za-z0-9])" + re.escape(tk) + r"(?![A-Za-z0-9])", premasked)
        return m.start() if m else len(premasked)

    coins = sorted(set(tickers) | paren_tickers(premasked), key=first_pos)
    return {tk: (f"<가상자산명{n}>", f"<티커{n}>")
            for i, tk in enumerate(coins, 1) for n in [str(i) if len(coins) > 1 else ""]}


def coin_slots(text: str, tickers=()) -> dict[str, str]:
    """mask_reference(text)가 각 티커에 붙이는 자리 이름. 예) {'NEO': '<가상자산명2>(<티커2>)'}"""
    return {tk: f"{c}({t})" for tk, (c, t) in _coin_masks(_premask(text), tickers).items()}


def mask_reference(text: str, tickers: set[str] | list[str] = ()) -> str:
    """참고 공지의 사실값을 가린다. 형식·어투는 남기고 복사될 값만 지운다.

    tickers: 이 공지의 가상자산 티커(색인 tickers 필드). 본문 속 '이름(TICKER)'도 함께 찾는다.
    """
    t = _premask(text)
    names: dict[str, str] = {}
    for tk, (coin_mask, tick_mask) in _coin_masks(t, tickers).items():
        named = re.compile(r"([가-힣A-Za-z0-9.\-]+)\s*\(\s*" + re.escape(tk) + r"\s*\)")
        names.update({m.group(1): coin_mask for m in named.finditer(t)})
        t = named.sub(f"{coin_mask}({tick_mask})", t)
        t = re.sub(r"(?<![A-Za-z0-9<])" + re.escape(tk) + r"(?![A-Za-z0-9>])", tick_mask, t)
    # 이름만 따로 쓰인 곳도 가린다. 2글자 이하는 일반 단어('링크' 등)와 겹쳐 제외.
    for name in sorted(names, key=len, reverse=True):
        if len(name) >= 3 and not name.isdigit():
            t = t.replace(name, names[name])
    return t


_LINE_BULLET_RE = re.compile(r"^[\s\-ㆍ·•*■※\[\]]+")
MIN_COPY_LINE = 30     # 이보다 짧은 줄은 복사 판정에서 뺀다(항목명·짧은 관용구)
COMMON_SIM = 0.8       # 다른 같은 유형 공지에 이만큼 비슷한 줄이 있으면 고정 문구(실측: 고정 문구 0.89~0.99, 약관 조문 0.3대)
COPY_SIM = 0.9         # 초안 줄이 고유 문장과 이만큼 비슷하면 복사로 본다


def _norm_line(line: str, tickers) -> str:
    """줄 비교용 정규화: 사실값 마스킹 + 마스크 번호 제거 + 글머리표·공백 정리."""
    s = mask_reference(line, tickers)
    s = re.sub(r"<(가상자산명|티커)\d*>", r"<\1>", s)
    return re.sub(r"\s+", " ", _LINE_BULLET_RE.sub("", s)).strip()


def _similar_any(s: str, pool, threshold: float) -> bool:
    for c in pool:
        sm = difflib.SequenceMatcher(None, s, c)
        if sm.real_quick_ratio() >= threshold and sm.quick_ratio() >= threshold and sm.ratio() >= threshold:
            return True
    return False


def specific_lines(ref_text: str, ref_tickers, others: list[tuple[str, set[str]]]) -> set[str]:
    """참고 공지에만 있고 같은 유형 다른 공지에는 비슷한 줄도 없는 긴 줄 = 그 사례 고유의 내용.

    인사말·유의사항처럼 여러 공지에 반복되는 문장(표현이 조금씩 달라도)은 빠진다.
    비교할 공지가 없으면 판단할 수 없어 빈 집합.
    """
    if not others:
        return set()
    common = {n for body, tk in others for ln in body.splitlines() if len(n := _norm_line(ln, tk)) >= 15}
    out = set()
    for ln in ref_text.splitlines():
        n = _norm_line(ln, ref_tickers)
        if len(n) >= MIN_COPY_LINE and n not in common and not _similar_any(n, common, COMMON_SIM):
            out.add(n)
    return out


# ── 초안 파싱 ───────────────────────────────────────────────────────────
_TITLE_LINE_RE = re.compile(r"^[\s*#]*제목[\s*]*[:：][\s*]*(.*)$", re.M)
_BODY_LINE_RE = re.compile(r"^[\s*#]*본문[\s*]*[:：][\s*]*", re.M)
_CONFIRM_HDR_RE = re.compile(r"^[\s*#]*\[?\s*확인\s*필요\s*사항\s*\]?[\s*]*:?\s*$", re.M)


def parse_draft(text: str) -> tuple[str, str, list[str], bool]:
    """LLM 출력 → (제목, 본문, 확인 필요 사항, 형식 인식 여부)."""
    text = text or ""
    tm, bm = _TITLE_LINE_RE.search(text), _BODY_LINE_RE.search(text)
    if not tm or not bm or bm.start() < tm.start():
        return "", text.strip(), [], False
    title = tm.group(1).strip().strip("*").strip()
    cm = _CONFIRM_HDR_RE.search(text, bm.end())
    body = text[bm.end(): cm.start() if cm else len(text)].strip()
    confirm = []
    if cm:
        for line in text[cm.end():].splitlines():
            s = line.strip().lstrip("-•*·ㆍ ").strip()
            if s:
                confirm.append(s)
    return title, body, confirm, True


# ── 초안 검증 ───────────────────────────────────────────────────────────
@dataclass
class Issue:
    code: str
    severity: str        # error | warn
    message: str


@dataclass
class DraftCheck:
    title: str
    body: str
    parsed: bool
    issues: list[Issue] = field(default_factory=list)
    needs_confirmation: list[str] = field(default_factory=list)

    @property
    def errors(self) -> list[Issue]:
        return [i for i in self.issues if i.severity == "error"]

    @property
    def warnings(self) -> list[Issue]:
        return [i for i in self.issues if i.severity == "warn"]

    def to_dict(self) -> dict:
        return {"title": self.title, "body": self.body, "parsed": self.parsed,
                "issues": [asdict(i) for i in self.issues], "needs_confirmation": self.needs_confirmation}


def _input_text(parts: list[Part]) -> str:
    """입력값 전체를 문자열로(날짜·시각·조항·링크 허용 목록을 여기서 뽑는다)."""
    chunks = []
    for p in parts:
        for name, v in p.inputs.items():
            if is_empty(v) or is_unknown(v):
                continue
            if isinstance(v, list):
                chunks.extend(f"{c.get('name','')}({c.get('ticker','')})" if isinstance(c, dict) else str(c)
                              for c in v)
            else:
                chunks.append(str(v))
                if name in FIELDS and is_temporal(FIELDS[name].kind):
                    chunks.append(render_datetime(v))
    return "\n".join(chunks)


def _date_allowed(y, mo, d, allowed) -> bool:
    return any(mo == am and d == ad and (y is None or ay is None or y == ay) for ay, am, ad in allowed)


def _datetime_present(value, doc_dates, doc_times, doc: str, date_only: bool = False) -> bool:
    if render_datetime(value, date_only) in doc:
        return True
    parsed = parse_datetime(value)
    if not parsed:
        return str(value) in doc
    dt, has_time = parsed
    if not any(mo == dt.month and d == dt.day and (y is None or y == dt.year) for y, mo, d, _, _ in doc_dates):
        return False
    return date_only or not has_time or any((h, mi) == (dt.hour, dt.minute) for h, mi, _ in doc_times)


def _line_at(doc: str, pos: int, clip: int = 80) -> str:
    """pos가 들어 있는 줄(오류 메시지에 인용용)."""
    start = doc.rfind("\n", 0, pos) + 1
    end = doc.find("\n", pos)
    line = doc[start: end if end != -1 else len(doc)].strip()
    return line if len(line) <= clip else line[:clip] + "…"


def _loose_in(value: str, doc: str) -> bool:
    squash = lambda s: re.sub(r"\s+", "", s)
    return squash(value) in squash(doc)


def _label_values(body: str, label_rx: str) -> list[str]:
    """'라벨 : 값' 또는 '라벨\\n- 값' 꼴에서 값 줄을 뽑는다(짧은 라벨 줄만 인정)."""
    lines = [ln.strip() for ln in body.splitlines()]
    out = []
    for i, line in enumerate(lines):
        s = line.lstrip("■ㆍ·-•*[ ").strip()
        head, sep, tail = s.partition(":")
        if not (len(head) <= 30 and re.search(label_rx, head)):
            continue
        if sep and tail.strip():
            out.append(tail.strip())
            continue
        nxt = next((ln for ln in lines[i + 1:] if ln), "")
        if nxt:
            out.append(nxt.lstrip("-ㆍ·•* ").strip())
    return out


def check_draft(
    draft: str,
    parts: list[Part],
    *,
    reference_tickers: set[str] = frozenset(),
    reference_specific: set[str] = frozenset(),
) -> DraftCheck:
    """reference_specific: specific_lines()로 구한 '참고 공지 고유 문장'(정규화된 줄)."""
    title, body, confirm, parsed = parse_draft(draft)
    doc = f"{title}\n{body}"
    chk = DraftCheck(title, body, parsed)
    add = lambda code, sev, msg: chk.issues.append(Issue(code, sev, msg)) \
        if not any(i.message == msg for i in chk.issues) else None

    if not parsed:
        add("format", "error", "출력 형식('제목:'/'본문:')을 찾을 수 없습니다.")
    if not body.strip():
        add("format", "error", "초안 본문이 비어 있습니다.")

    in_text = _input_text(parts)
    in_tickers = {c["ticker"] for p in parts for n, v in p.inputs.items()
                  if n in FIELDS and FIELDS[n].kind == "coins" and isinstance(v, list) for c in v}
    in_tickers |= paren_tickers(in_text)
    allowed_dates = {(y, mo, d) for y, mo, d, _, _ in extract_dates(in_text)}
    allowed_times = {(h, mi) for h, mi, _ in extract_times(in_text)}
    doc_dates, doc_times = extract_dates(doc), extract_times(doc)

    # 1) 입력값이 초안에 반영됐는가 — 필수든 선택이든 사용자가 준 사실값(코인·일시·링크·고유명)은 들어가야 한다
    for p in parts:
        for name in p.ntype.fields:
            v = p.inputs.get(name)
            if is_empty(v) or is_unknown(v):
                continue
            f, label = FIELDS[name], field_label(p.ntype, name)
            if f.kind == "coins":
                for c in v:
                    for token in (c.get("name"), c.get("ticker")):
                        if token and token not in doc:
                            add("missing_value", "error", f"{label} '{token}'이(가) 초안에 없습니다.")
            elif is_temporal(f.kind):
                if not _datetime_present(v, doc_dates, doc_times, doc, f.kind == "date"):
                    add("missing_value", "error", f"{label} '{display_value(name, v)}'이(가) 초안에 없습니다.")
            elif f.kind == "urls":
                for u in v:
                    if u not in doc:
                        add("missing_value", "error", f"{label} '{u}'이(가) 초안에 없습니다.")
            elif f.exact and not _loose_in(str(v), doc):
                # 쉼표로 여러 개면 각각 확인(표기 순서·구분자는 달라도 됨)
                missing = [x for x in re.split(r"\s*,\s*", str(v)) if x and not _loose_in(x, doc)]
                if missing:
                    add("missing_value", "error", f"{label} '{', '.join(missing)}'이(가) 초안에 없습니다.")

    # 2) 다른 가상자산 혼입
    for t in sorted(paren_tickers(doc) - in_tickers):
        if t in reference_tickers:
            add("foreign_coin", "error", f"참고 공지의 가상자산 '{t}'가 초안에 섞였습니다.")
        else:
            add("foreign_coin", "warn", f"입력에 없는 티커/약어 '{t}'가 있습니다. 확인 필요.")
    for t in sorted(set(reference_tickers) - in_tickers):
        if len(t) >= 3 and re.search(r"(?<![A-Za-z0-9])" + re.escape(t) + r"(?![A-Za-z0-9])", doc):
            add("foreign_coin", "error", f"참고 공지의 가상자산 '{t}'가 초안에 섞였습니다.")

    # 3) 입력에 없는 날짜·시각, 요일 오류
    for y, mo, d, wd, raw in doc_dates:
        if not _date_allowed(y, mo, d, allowed_dates):
            add("unknown_date", "error", f"입력에 없는 날짜 '{raw}'가 있습니다.")
            continue
        year = y or next((ay for ay, am, ad in allowed_dates if (am, ad) == (mo, d) and ay), None)
        if wd and year:
            real = _WEEKDAYS[date(year, mo, d).weekday()]
            if real != wd:
                add("weekday", "error", f"요일 오류: '{raw}'는 {real}요일입니다.")
    for h, mi, raw in doc_times:
        if (h, mi) not in allowed_times:
            add("unknown_time", "error", f"입력에 없는 시각 '{raw.strip()}'가 있습니다.")

    # 4) 제목의 '(MM/DD 재개)' — 재개 일시를 입력하지 않았는데 날짜를 채운 경우
    resume = [p.inputs["resume_at"] for p in parts
              if not is_empty(p.inputs.get("resume_at")) and not is_unknown(p.inputs.get("resume_at"))]
    for m in _TITLE_RESUME_RE.finditer(title):
        md = (int(m.group(1)), int(m.group(2)))
        parsed_resume = [parse_datetime(r) for r in resume]
        if not resume:
            add("resume_date", "error", f"재개 일시를 입력하지 않았는데 제목에 재개 날짜 '{m.group(0)})'가 있습니다.")
        elif not any(pr and (pr[0].month, pr[0].day) == md for pr in parsed_resume):
            add("resume_date", "error", f"제목의 재개 날짜 '{m.group(0)})'가 입력한 재개 일시와 다릅니다.")

    # 5) 라벨 줄 값 대조(입력 안 한 항목에 값이 채워졌는가 / 다른 날짜가 들어갔는가)
    for p in parts:
        for label_rx, name in p.ntype.labels:
            f, v, label = FIELDS[name], p.inputs.get(name), field_label(p.ntype, name)
            for value in _label_values(body, label_rx):
                if is_empty(v) or is_unknown(v):
                    if is_temporal(f.kind) and (extract_dates(value) or extract_times(value)):
                        add("label_value", "error", f"입력하지 않은 '{label}'에 날짜/시각이 채워졌습니다: '{value}'")
                    elif not is_temporal(f.kind) and not re.search(r"\[|확인|추후|별도|미정", value):
                        add("label_value", "error", f"입력하지 않은 '{label}' 값이 채워졌습니다: '{value}'")
                elif is_temporal(f.kind):
                    pv = parse_datetime(v)
                    vd = extract_dates(value)
                    if pv and vd and not any((mo, d) == (pv[0].month, pv[0].day) for _, mo, d, _, _ in vd):
                        add("label_value", "error",
                            f"'{label}' 값이 입력({display_value(name, v)})과 다릅니다: '{value}'")

    # 6) 링크
    in_urls = set(_URL_RE.findall(in_text))
    for u in _URL_RE.findall(doc):
        if u.rstrip(".,") not in in_urls:
            add("unknown_url", "error", f"입력에 없는 링크 '{u}'가 있습니다.")

    # 7) 법령 조항 — 입력에 없는 조항은 오류, 조항이 있으면 항상 검증 필요 경고
    for c in sorted(clauses(doc) - clauses(in_text)):
        add("law_clause", "error", f"입력에 없는 법령 조항 '{c}'가 있습니다(참고 공지 복사·추측 금지).")
    in_approvals = {re.sub(r"\s+", "", a) for a in _APPROVAL_RE.findall(in_text)}
    for a in _APPROVAL_RE.findall(doc):
        if re.sub(r"\s+", "", a) not in in_approvals:
            add("unknown_number", "error", f"입력에 없는 심사필 번호 '{a}'가 있습니다.")
    if _LAW_RE.search(doc):
        add("law_clause", "warn", "초안에 법령/규정 조항이 포함되어 있습니다. 사유에 맞는 조항인지 반드시 검증 필요.")

    # 8) 채워지지 않은 자리표시자([확인 필요]는 의도된 표기라 제외)
    for m in _PLACEHOLDER_RE.finditer(doc):
        add("placeholder", "error", f"채워지지 않은 자리표시자 '{m.group(0)}'가 남아 있습니다.")

    # 9) 필수 항목(표현이 다양해 경고만)
    for p in parts:
        for name, rx in p.ntype.sections:
            if not re.search(rx, doc):
                add("section", "warn", f"'{p.ntype.label}' 필수 항목이 보이지 않습니다: {name}")

    # 10) 유형별 금지 서술(해당 입력이 없을 때)
    for p in parts:
        for name, rx in p.ntype.guards:
            v = p.inputs.get(name)
            m = re.search(rx, body) if (is_empty(v) or is_unknown(v)) else None
            if m:
                add("guard", "error", f"입력하지 않은 '{field_label(p.ntype, name)}' 관련 사실이 있습니다: '{m.group(0)}'")

    # 11) 역할 자리의 가상자산(예: 에어드랍 '(티커) 보유자'는 보유 기준 가상자산이어야 함)
    #     수정 LLM이 바로 고칠 수 있게 틀린 문장 전체와 정답을 함께 적는다
    #     (자리만 알려주면 다른 줄에 맞는 값이 있다는 이유로 그 문장을 고치지 않은 사례가 있었다)
    for p in parts:
        for rx, name in p.ntype.roles:
            coins = [c for c in (p.inputs.get(name) or []) if isinstance(c, dict)]
            allowed = {c["ticker"] for c in coins}
            for m in re.finditer(rx, doc) if allowed else ():
                if m.group("ticker") not in allowed and m.group("ticker") not in _NON_COIN:
                    want = coin_label(coins)
                    rest = m.group(0).split(")", 1)[1].strip()
                    add("role", "error",
                        f"'{_line_at(doc, m.start())}' 문장의 '{m.group(0)}'가 틀렸습니다. "
                        f"{field_label(p.ntype, name)}은(는) {want}이므로 '{want} {rest}'로 고치세요.")

    # 12) 참고 공지에만 있는 내용(사례 고유 문장)을 그대로 옮겼는가
    if reference_specific:
        tickers = reference_tickers | in_tickers
        copied = list(dict.fromkeys(
            n for ln in body.splitlines()
            if len(n := _norm_line(ln, tickers)) >= MIN_COPY_LINE and not _loose_in(ln.strip(), in_text)
            and (n in reference_specific or _similar_any(n, reference_specific, COPY_SIM))))
        if copied:
            sample = copied[0][:50]
            add("reference_copy", "error" if len(copied) >= 2 else "warn",
                f"참고 공지에만 있는 내용 {len(copied)}줄이 그대로 들어갔습니다(예: '{sample}…'). "
                "입력값에 없는 내용이면 빼야 합니다.")

    marks = [m.group(0) for m in _CONFIRM_MARK_RE.finditer(doc)]
    chk.needs_confirmation = list(dict.fromkeys(marks + confirm))
    return chk
