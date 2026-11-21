"""수집 데이터(starbucks/location, details)로 전국 매장 데이터(starbucks/stores.json)를 처음 생성한다.

stores.json 은 직접 관리하는 파일이므로 자동 실행하지 않는다.
이미 파일이 있으면 덮어쓰지 않으며, 전체를 다시 만들 때만 --force 로 실행한다.
(신규·폐점 매장 확인은 starbucks-changes.py → starbucks/changes/latest.json)

실행: python starbucks-stores.py --force

입력
- starbucks/location/{지역}/latest.json : 현재 영업 매장 목록 (기준)
- starbucks/details/{지역}/latest.json  : 매장 상세 정보
- starbucks/stores.json                 : 이전 결과 (위치 데이터를 못 읽은 날 대비용)
- starbucks/stores-back.json            : 수동 조사값 백업

출력
- starbucks/stores.json                 : 전국 현재 영업 매장

값이 없는 항목은 결과에 넣지 않는다.
"""

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
import json
import re
import sys

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "starbucks"
STORES_DIR = DATA_DIR

# 지역 순서 (starbucks-details.py 의 REGIONS 와 동일)
REGIONS = [
    "seoul", "gyeonggi", "incheon", "gangwon", "daejeon", "sejong", "chungnam", "chungbuk",
    "daegu", "gyeongbuk", "busan", "ulsan", "gyeongnam", "jeonbuk", "jeonnam-gwangju", "jeju",
]

# 스타벅스 매장 찾기 페이지의 테마 코드 → 이름
LOCATION_CODES = {
    "P10": "공항", "P50": "해안가", "P20": "대학가", "P60": "터미널·기차역",
    "P30": "리조트", "P70": "병원", "P80": "지하철인접",
}
SERVICE_CODES = {
    "T30": "블론드", "T05": "피지오", "T22": "나이트로콜드브루", "T21": "현금없는매장",
    "T10": "외화결제", "T36": "위생등급인증", "T43": "딜리버스", "T48": "에코매장",
    "Z9999": "21시이후영업", "T64": "패스트서브", "T66": "패스트서브",
    "P02": "펫존", "WHCROAD": "장애인편의시설", "P90": "공기청정기", "P01": "전기차충전",
}

# theme_codes 가 없는 예전 상세 데이터용: 아이콘 파일명 → 코드
# services 에 넣을 전체 라벨 (상세의 services + facilities 아이콘)
SERVICE_LABELS = {**SERVICE_CODES, "T09": "주차", "P40": "입점", **LOCATION_CODES}

ICON_CODES = {
    "icon04.png": "T09", "icon05.png": "T10", "icon18.png": "T22", "icon20.png": "T21",
    "icon22.png": "T05", "icon23.png": "T30", "icon_delivers_service.png": "T43",
    "icon_eco_service.png": "T48", "Moon_icon_16x16_230324.png": "Z9999",
    "icon_fast_serve_02.png": "T64", "icon_pet_01.png": "P02", "icon07.png": "P10",
    "icon09.png": "P50", "icon08.png": "P20", "icon12.png": "P60", "icon13.png": "P70",
    "icon10.png": "P40", "icon14.png": "P80", "icon21.png": "P90", "EV_icon_map.png": "P01",
    "icon19.png": "WHCROAD", "icon11.png": "P30",
}

# ── 주차 ──────────────────────────────────────────────────────────
# 스타벅스 매장 parking 원문 텍스트를 정형 데이터로 변환한다.
#
# 결과 형식:
# {
#     "available": "주차가능" | "주차불가능" | None,
#     "fee": "무료" | "조건부무료" | "유료" | None,
#     "spaces": "10대 이상" | None,       # 주차댓수
#     "registration": "..." | None,       # 주차등록방법 (원문)
#     "registration_type": "파트너요청" | "태블릿·키오스크" | "사전정산기" | "영수증제시" | "불필요" | None,
#     "payment": "..." | None,            # 주차정산방법 (원문)
#     "payment_type": "정산없이출차" | "사전정산기" | "출차시영수증제시" | "출차시정산" | None,
#     "raw": "원문"
# }

# "1. 주차가능대수 : 10대" 같은 항목 라벨 → 표준 키
LABELS = [
    ("payment", r"(?:주차\s*)?(?:요금\s*)?정산\s*(?:방법)?"),
    ("spaces", r"주차\s*(?:가능\s*)?대수"),
    ("location", r"주차\s*(?:장\s*)?(?:가능\s*)?위치|위치"),
    ("condition", r"주차\s*(?:조건|요금)"),
    ("registration", r"주차\s*등록\s*(?:방법)?|등록\s*방법"),
    ("note", r"특이\s*사항|기타"),
]

UNAVAILABLE = re.compile(r"주차\s*(?:가\s*)?(?:불가|불가능|안\s*됨|지원\s*불가|공간\s*없음)|무료\s*주차\s*지원\s*불가|주차장\s*(?:이\s*)?없|^\s*(?:주차\s*)?불가?\s*$|주차\s*불\b|^\s*별도\s*지원\s*불가|주차\s*미지원|이용\s*(?:이\s*)?(?:절대\s*)?불가")
AVAILABLE = re.compile(
    r"주[차가]\s*(?:가능|지원|공간|제공)|유료\s*주차|무료|주차장|\d+\s*대|"
    r"\d+\s*(?:시간|분)\s*(?:이용|주차)|입차\s*가능"
)
CONDITION_HINT = re.compile(
    r"\d+\s*(?:만\s*)?원\s*이상|구매\s*시|구매\s*고객|이용\s*시|영수증|최대|"
    r"\d+\s*(?:시간|분)\s*(?:까지\s*)?무료|무료\s*\d+\s*(?:시간|분)|초과|이후"
)
PAID_HINT = re.compile(r"유료|\d[\d,]*\s*원\s*(?:/|per|당)|분\s*당|시간\s*당|요금\s*발생|추가\s*요금|\d+\s*분\s*\d[\d,]*\s*원")
REGISTRATION_HINT = re.compile(r"등록|차량\s*번호|키오스크|주차권|도장|파트너에게|카운터|직원에게|태블릿|웹\s*할인|QR")


def clean(text):
    if text is None:
        return None
    text = re.sub(r"\s+", " ", text).strip(" -:_/,.·")
    return text or None


def split_fields(raw):
    """번호 매긴 항목을 라벨별로 분리한다. 라벨이 없는 줄은 'other'로 모은다."""
    fields = {}
    # "1. ", "2) ", "\n" 기준으로 줄을 나눈다.
    parts = re.split(r"\n+|(?:^|\s)(?=\d\s*[.)]\s*\D)", raw)
    for part in parts:
        line = re.sub(r"^\s*\d\s*[.)]\s*", "", part).strip()
        if not line:
            continue
        for key, pattern in LABELS:
            match = re.match(rf"({pattern})\s*[:：\-–_=]?\s*(.*)$", line)
            if match:
                fields.setdefault(key, []).append(match.group(2))
                break
        else:
            fields.setdefault("other", []).append(line)
    return {key: " ".join(values) for key, values in fields.items()}


def parse_spaces(fields, raw):
    text = fields.get("spaces")
    if text is None:
        match = re.search(r"(\d[\d,]*\s*대\s*(?:이상|미만|이하|내외)?)", raw)
        text = match.group(1) if match else None
    if not text:
        return None
    text = re.sub(r"\([^)]*\)", " ", text)  # "(장애인 2대 포함)" 같은 괄호 제외
    match = re.search(r"(?:총\s*)?(\d[\d,]*)\s*(?:대)?\s*(이상|미만|이하|내외)?", text)
    if not match:
        return clean(text)
    suffix = f" {match.group(2)}" if match.group(2) else ""
    return f"{match.group(1).replace(',', '')}대{suffix}"


FEE_RULES = [
    # (판정, 패턴) — 위에서부터 먼저 맞는 규칙을 쓴다.
    ("유료", r"(?:요금|주차)\s*지원\s*(?:불가|없음)|별도\s*(?:주차\s*)?지원\s*없|요금\s*(?:부과|정책에\s*따)|유료\s*주차"),
    ("조건부무료",
     r"(?:구매|결제|이상|이하|당|주문|이용)[^,/|]{0,14}?\d+\s*(?:시간|분)"
     r"|\d+\s*(?:시간|분)\s*(?:무료|제공|등록|지원|이용|주차|가능)"
     r"|(?:등록|최대)\s*\(?\s*\d+\s*(?:시간|분)"
     r"|구매\s*(?:고객|시|후)[^,/|]{0,10}(?:가능|한함|사용)|매장\s*이용\s*시\s*(?:주차\s*)?가능"
     r"|주차\s*(?:할인)?권|주차증|영수증\s*(?:지참|제시|합산)"),
    ("무료", r"(?:시간\s*)?제한\s*(?:시간\s*)?없|조건\s*없|비용[^,/|]{0,8}없|정산\s*(?:필요\s*)?없|^\s*(?:없음|무|무료)\s*$"),
]


def parse_fee(text):
    if not text:
        return None
    # "회차 30분 무료" 같은 회차(바로 출차) 무료 구절만 제거한다.
    text = re.sub(r"(?:최초\s*|기본\s*)?(?:회차\s*\d+\s*분|\d+\s*분\s*(?:무료\s*)?회차)(?:\s*무료)?", " ", text)
    for verdict, pattern in FEE_RULES:
        if re.search(pattern, text):
            if verdict == "무료" and PAID_HINT.search(text):
                return "조건부무료"
            return verdict
    has_free = "무료" in text and not re.search(r"무료\s*(?:주차\s*)?(?:지원\s*)?(?:불가|없음)", text)
    if has_free:
        if CONDITION_HINT.search(text) or PAID_HINT.search(text):
            return "조건부무료"
        return "무료"
    if PAID_HINT.search(text) or re.search(r"\d[\d,]*\s*원", text):
        return "유료"
    return None


PAYMENT_HINT = re.compile(r"출차|정산\s*기|사전\s*정산|무인|카드|현금|결[제재]|정산\s*(?:없|필요\s*없|X)|자동\s*정산|하이패스|앱")


def parse_payment(fields):
    """정산 항목 중 '출차 시 어떻게 계산하는지'에 해당하는 문장만 남긴다."""
    text = fields.get("payment") or ""
    parts = [part for part in re.split(r"[/,]|\s(?=\()", text) if PAYMENT_HINT.search(part)]
    return clean(", ".join(parts)) if parts else None


REGISTRATION_TYPES = [
    # (분류값, 패턴) — 위에서부터 먼저 맞는 값을 쓴다.
    ("불필요", r"등록\s*(?:필요\s*)?(?:없|불필요|X)|정산\s*(?:없이|필요\s*없)|확인\s*없이\s*출차|별도\s*등록\s*없"),
    ("태블릿·키오스크", r"태블릿|테블릿|키오스크|kiosk|패드|PC"),
    ("사전정산기", r"사전\s*정산|정산기|무인\s*정산"),
    ("영수증제시", r"영수증\s*(?:지참|제시|확인|으로)|주차\s*(?:할인)?권|주차증|도장"),
    ("파트너요청", r"파트너|직원|매장\s*(?:에|내)?\s*(?:등록|문의|요청)|카운터|등록\s*요청"),
    ("파트너요청", r"매장|포스|POS|웹\s*할인|등록"),  # 매장에서 등록해 주는 방식
]

PAYMENT_TYPES = [
    ("정산없이출차", r"정산\s*(?:없이|필요\s*없|X|없음)|확인\s*없이\s*출차|자동\s*(?:정산|출차)|번호\s*인식"),
    ("사전정산기", r"사전\s*정산|정산기|무인"),
    ("출차시영수증제시", r"출차\s*시?\s*[^,]{0,6}영수증|영수증\s*제시"),
    ("출차시정산", r"출차|결[제재]|카드|현금|경비실|관리실|요금소"),
]


def classify(text, rules):
    if not text:
        return None
    for value, pattern in rules:
        if re.search(pattern, text, re.I):
            return value
    return None


def parse_registration(fields, raw):
    if fields.get("registration"):
        return clean(fields["registration"])
    # 조건·정산·기타 문장에서 등록 방법에 해당하는 구절을 찾는다.
    for key in ("condition", "payment", "note", "other"):
        text = fields.get(key) or ""
        for sentence in re.split(r"[/\n]|(?<=[.)])\s|,\s*(?=\D)", text):
            if REGISTRATION_HINT.search(sentence) and not UNAVAILABLE.search(sentence):
                return clean(sentence)
    return None


def parse_parking(raw):
    raw = raw or ""
    result = {
        "available": None, "fee": None, "spaces": None,
        "registration": None, "registration_type": None,
        "payment": None, "payment_type": None, "raw": raw.strip(),
    }
    if not raw.strip():
        return result

    if re.fullmatch(r"\s*(?:주차\s*)?불가?\s*", raw):
        result["available"] = "주차불가능"
        return result
    fields = split_fields(raw)
    head = fields.get("other", "") + " " + raw[:40]
    if UNAVAILABLE.search(head):
        result["available"] = "주차불가능"
        return result
    if AVAILABLE.search(raw):
        result["available"] = "주차가능"
    else:
        return result

    result["spaces"] = parse_spaces(fields, raw)
    # 조건 항목을 우선 보고, 없으면 등록·정산·기타 문장까지 본다.
    result["fee"] = (
        parse_fee(clean(fields.get("condition")))
        or parse_fee(" / ".join(filter(None, [fields.get(k) for k in ("registration", "payment", "note", "other")])))
        or parse_fee(raw)  # 항목 구분 없이 한 줄에 몰아 쓴 경우
    )
    result["payment"] = parse_payment(fields)
    result["registration"] = parse_registration(fields, raw)
    result["registration_type"] = classify(result["registration"], REGISTRATION_TYPES)
    result["payment_type"] = classify(result["payment"], PAYMENT_TYPES)
    # 무료(조건 없음)이고 등록 문구가 없으면 등록 불필요로 본다.
    if result["fee"] == "무료" and not result["registration_type"]:
        result["registration_type"] = "불필요"
    return result



# ── 스타벅스 주차 (할리스는 위 parse_parking 형식을 그대로 쓴다) ──────────
# available 주차가능/주차불가능, fee 무료/유료/조건부무료, location 주차장 위치, spaces 50대 이상,
# condition 무료 조건(2시간 무료), method 요금(초과 시 10분당 2000원), payment 정산,
# memo 위 항목에 해당하지 않는 내용, raw 원본
STARBUCKS_PARKING = ("available", "fee", "location", "spaces", "condition", "method", "payment", "memo", "raw")

FREE_TIME = re.compile(r"\d+\s*(?:시간|분)\s*(?:\([^)]*\)\s*)?(?:최초\s*)?(?:무료|무상|지원|제공|등록)|무료\s*\d+\s*(?:시간|분)")
RATE = re.compile(r"((?:\d+\s*)?(?:시간|분))\s*당\s*([\d,]+)\s*원")
SEGMENT_SPLIT = re.compile(r"[/|\n]|,(?!\d{3})|\.\s|\s(?=\()|(?<=\))\s")
# 남겨도 의미 없는 짧은 조각
TRIVIAL = re.compile(r"(?:(?:조건부\s*)?(?:무료|유료)(?:\s*주차)?|추가|이후|발생|부과|초과\s*시?|요금|비용|씩|원|O|X|없음|[-.\s])+")


def parse_rate(raw):
    rates = []
    for unit, price in RATE.findall(raw or ""):
        unit = re.sub(r"\s+", "", unit)
        rate = f"{'1' if unit == '시간' else ''}{unit}당 {price.replace(',', '')}원"
        if rate not in rates:
            rates.append(rate)
    return ", ".join(rates) or None


def parking_parts(raw):
    """원본을 줄(항목) 단위로 나눠 [(라벨, 내용)] 으로 반환한다. 라벨 규칙은 split_fields 와 같다."""
    parts = []
    for part in re.split(r"\n+|(?:^|\s)(?=\d\s*[.)]\s*\D)", raw or ""):
        line = re.sub(r"^\s*\d\s*[.)]\s*", "", part).strip()
        if not line:
            continue
        for key, pattern in LABELS:
            match = re.match(rf"({pattern})\s*[:：\-–_=]?\s*(.*)$", line)
            if match:
                parts.append((key, match.group(2).strip()))
                break
        else:
            parts.append(("other", line))
    return parts


def tidy(segment):
    """조각 앞뒤의 짝이 맞지 않는 괄호와 군더더기를 정리한다."""
    segment = re.sub(r"조건부\s*무료\s*|^\s*(?:주차\s*)?조건\s*[:：\-]\s*", "", segment).strip()
    while segment.startswith("(") and segment.endswith(")"):
        segment = segment[1:-1].strip()
    if segment.startswith("(") and ")" not in segment:
        segment = segment[1:]
    if segment.endswith(")") and "(" not in segment:
        segment = segment[:-1]
    if segment.count("(") > segment.count(")"):
        segment = segment + ")"
    segment = clean(segment)
    if not segment or TRIVIAL.fullmatch(segment):
        return None
    return segment


def split_condition(text):
    """조건 문장을 무료 조건 구절과 나머지 구절로 나눈다."""
    free, rest = [], []
    for segment in SEGMENT_SPLIT.split(text or ""):
        segment = RATE.sub("", segment)
        # 요금을 빼고 남은 "(이후 )", "(1시간 경과시)" 같은 빈 괄호를 지운다.
        segment = re.sub(r"\(([^)]*)\)", lambda m: "" if not m.group(1).strip() or TRIVIAL.fullmatch(m.group(1).strip())
                         or re.fullmatch(r"\d+\s*(?:시간|분)\s*(?:경과|초과)\s*시?", m.group(1).strip()) else m.group(0), segment)
        segment = re.sub(r"\(\s*(?:\d+\s*(?:시간|분)\s*)?(?:경과|초과|이후)?\s*시?\s*$", "", segment)  # 닫히지 않은 빈 괄호
        segment = re.sub(r"(?:이후|초과\s*시?|시간\s*초과\s*시?)\s*$", "", segment.strip())
        segment = tidy(segment)
        if not segment:
            continue
        target = free if FREE_TIME.search(segment) else rest
        if segment not in target:
            target.append(segment)
    return free, rest


def parse_parking_location(raw):
    """'주차장 정보 - ...' 라벨이나, 라벨 없이 쓴 2번 항목(1.주차가능 2.건물 지하층)을 위치로 본다."""
    match = re.search(r"주차장?\s*정보\s*[:：\-–]?\s*([^\n]+)", raw or "")
    if match:
        return clean(match.group(1))
    lines = [line.strip() for line in (raw or "").splitlines()]
    if len(lines) >= 2 and re.match(r"1\s*[.)]\s*주차\s*가능\s*$", lines[0]):
        match = re.match(r"2\s*[.)]\s*(.+)$", lines[1])
        if match and not re.search(r"\d+\s*대|무료|유료|정산|조건", match.group(1)):
            return clean(match.group(1))
    return None


def is_basic_line(text, result):
    """주차 여부·위치·대수처럼 다른 항목에 이미 들어간 줄인지 확인한다."""
    text = clean(text) or ""
    if re.fullmatch(r"(?:매장\s*)?주차\s*(?:가능|불가능?)\s*[.!]?", text):
        return True
    if result["location"] and (text == result["location"] or "주차장 정보" in text or "주차장정보" in text):
        return True
    if re.search(r"\d+\s*대", text) and len(text) <= 25 and not re.search(r"무료|유료|원|시간|분", text):
        return True
    return False


def parse_starbucks_parking(raw):
    base = parse_parking(raw)
    result = dict.fromkeys(STARBUCKS_PARKING)
    result.update(available=base["available"], fee=base["fee"], spaces=base["spaces"])
    result["raw"] = clean_text(raw)
    if base["available"] != "주차가능":
        return result

    parts = parking_parts(raw)
    first = lambda key: next((text for k, text in parts if k == key and clean(text)), None)
    result["location"] = clean(first("location")) or parse_parking_location(raw)
    memo = []
    used = set()

    # 정산: 정산 항목, 없으면 등록 방법. "정산O" 처럼 한두 글자뿐이면 memo 로 보낸다.
    payment_key = "payment" if first("payment") else "registration"
    payment = clean(first(payment_key))
    if payment:
        used.add((payment_key, first(payment_key)))
        if len(payment) <= 2:
            memo.append(f"정산 {payment}")
            payment = None
    result["payment"] = payment

    # 조건: 조건 항목 → 기타·라벨 없는 줄 → 등록·정산 순서로 무료 조건 구절을 찾는다.
    if base["fee"] in ("조건부무료", "무료"):
        order = ("condition", "note", "other", "registration", "payment")
        candidates = sorted(
            (part for part in parts if part[0] in order and not is_basic_line(part[1], result)),
            key=lambda part: order.index(part[0]),
        )
        for key, text in candidates:
            free, rest = split_condition(text)
            if free:
                result["condition"] = ", ".join(free)
                memo.extend(rest)
                used.add((key, text))
                break
        else:
            text = first("condition")
            if base["fee"] == "조건부무료" and text:
                result["condition"] = tidy(RATE.sub("", text))
                used.add(("condition", text))

    result["method"] = parse_rate(raw)
    # 무료 시간이 있는 매장의 요금은 무료 시간을 넘겼을 때 내는 요금이다.
    if result["method"] and base["fee"] in ("조건부무료", "무료"):
        result["method"] = f"초과 시 {result['method']}"

    # 나머지: 어느 항목에도 쓰이지 않은 줄
    for key, text in parts:
        if key == "spaces":  # "50대 이상(일요일 운영X)" 같은 괄호 속 안내는 남긴다
            memo.extend(seg for seg in map(tidy, re.findall(r"\(([^)]*)\)", text)) if seg and seg not in memo)
            continue
        if (key, text) in used or key == "location" or is_basic_line(text, result):
            continue
        if key in ("payment", "registration") and clean(text) == result["payment"]:
            continue
        if key == "condition":
            free, rest = split_condition(text)
            rest = free + rest  # 무료 조건으로 쓰이지 않은 조건 문장은 통째로 남긴다
        else:
            rest = [tidy(RATE.sub("", text))]
        for segment in rest:
            if segment and segment not in memo and segment != result["condition"]:
                memo.append(segment)
    result["memo"] = " / ".join(memo) or None
    return result


def parse_simple_parking(raw, icon=False):
    """할리스·투썸처럼 한 줄로 짧게 쓴 주차 정보를 스타벅스 주차 형식으로 바꾼다.
    icon: 사이트에 주차 아이콘(서비스)이 있는지"""
    text = clean(raw) or ""
    if re.search(r"불가|없습니다|없음|공간\s*(?:이\s*)?없|^X$", text) and not re.search(r"(?<!불)가능", text):
        result = dict.fromkeys(STARBUCKS_PARKING)
        result.update(available="주차불가능", raw=clean_text(raw))
        return result
    result = parse_starbucks_parking("주차가능" if re.fullmatch(r"가능|O|있음|유", text) else raw)
    result["raw"] = clean_text(raw)
    if result["available"] is None and (icon or re.search(r"(?<!불)가능|이용|有|있", text)):
        result["available"] = "주차가능"
    return result

# ── 뷰 ────────────────────────────────────────────────────────────
VIEW_KEYWORDS = {
    "바다": r"바다|오션|해변|해수욕장|해안|비치|수평선|부산항|앞바다|바닷",
    "강": r"한강|금강|남한강|북한강|낙동강|영산강|수영강|섬진강|강변|강\s*뷰",
    "호수": r"호수|저수지",
    "산": r"한라산|남산|산\s*뷰|산세|산자락|구봉산|앞산",
    "시티": r"시티\s*뷰|야경|스카이라인|도심\s*전망|최고층|서울타워|83타워|불꽃",
}
VIEW_CONTEXT = re.compile(
    r"뷰|view|보이|바라|감상|한눈|한 눈|펼쳐|전망|조망|눈앞|눈 앞|창밖|품은|경치|풍경|"
    r"내려다|즐기|즐길|즐겨|만끽|담으|담아|함께하|어우러"
)
# 지명·상호에 들어간 단어는 뷰로 보지 않는다.
VIEW_FALSE = re.compile(r"오션시티|한강신도시|한강로|비치타운|해안로|강변역|강변북로")


def detect_view(text, name=""):
    found = []
    text = text or ""
    if name:
        # "스타벅스 강변점", "안동강변DT 점" 처럼 매장 이름에 들어간 단어는 제외한다.
        text = re.sub(re.escape(name) + r"\s*(?:점|DT|입니다)", " ", text)
    for sentence in re.split(r"[.!?\n~]|(?<=니다)\s|(?<=세요)\s", text):
        if not VIEW_CONTEXT.search(sentence):
            continue
        cleaned = VIEW_FALSE.sub(" ", sentence)
        for name, pattern in VIEW_KEYWORDS.items():
            if name not in found and re.search(pattern, cleaned):
                found.append(name)
    return found


# ── 층 ────────────────────────────────────────────────────────────
FLOOR_PATTERN = re.compile(
    r"(지하\s*|B)?(\d{1,2})\s*(?:층|F)?\s*[~∼\-]\s*(지하\s*|B)?(\d{1,2})\s*(?:층|F)"
    r"|(지하\s*|B)?(\d{1,2})\s*(?:층|F)(?![A-Za-z])"
)


def floor_label(basement, number):
    return f"B{number}" if basement else f"{number}F"


def floor_range(b1, n1, b2, n2):
    """지하는 음수로 바꿔 범위를 펼친다. 예: B1~2F → B1, 1F, 2F"""
    start = -n1 if b1 else n1
    end = -n2 if b2 else n2
    if start > end:
        start, end = end, start
    labels = []
    for value in range(start, end + 1):
        if value != 0:
            labels.append(floor_label(value < 0, abs(value)))
    return labels


def parse_floors(text, parking_context=True):
    floors = []
    for match in FLOOR_PATTERN.finditer(text or ""):
        # 주차장 층수 설명은 제외한다.
        around = text[max(0, match.start() - 6):match.end() + 6]
        if parking_context and "주차" in around:
            continue
        if match.group(2):
            n1, n2 = int(match.group(2)), int(match.group(4))
            if max(n1, n2) > 60 or abs(n1 - n2) > 5:
                continue
            labels = floor_range(match.group(1), n1, match.group(3), n2)
        else:
            number = int(match.group(6))
            if number == 0 or number > 60:
                continue
            labels = [floor_label(match.group(5), number)]
        for label in labels:
            if label not in floors:
                floors.append(label)
    return floors


def sort_floors(floors):
    return sorted(floors, key=lambda f: -int(f[1:]) if f.startswith("B") else int(f[:-1]))


# ── 공통 ──────────────────────────────────────────────────────────
def load_json(path, default=None):
    try:
        with path.open(encoding="utf-8") as file:
            return json.load(file)
    except (OSError, json.JSONDecodeError):
        return default


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as file:
        json.dump(data, file, ensure_ascii=False, indent=4)
        file.write("\n")


# 모든 매장이 같은 항목을 갖도록 하는 기본 형태. 정보가 없으면 null 로 남긴다.
# 목록 항목(type/location/services)은 공식 정보 기준이라 해당 없음이면 [] 이다.
STORE_TEMPLATE = {
    "id": None, "name": None, "address": None, "directions": None, "description": None,
    "lat": None, "lng": None,
    "floors": None, "location": [], "type": [], "opened": None, "view": None,
    "seats": None, "smoking": None, "pet": None, "study": None, "phone": None,
    "services": [], "parking": None, "images": [],
}
PARKING_TEMPLATE = {
    "available": None, "fee": None, "spaces": None,
    "registration_type": None, "registration": None, "payment_type": None, "payment": None,
}


def clean_text(text):
    """줄마다 앞뒤 공백과 연속 공백을 정리하고 빈 줄은 뺀다."""
    lines = (re.sub(r"[ \t\u00a0]+", " ", line).strip() for line in (text or "").splitlines())
    return "\n".join(line for line in lines if line) or None


# 스타벅스 stores.json 에서 쓰지 않는 항목 (할리스는 그대로 사용)
STARBUCKS_DROP = ("floors", "location")


def normalize(store, drop=(), parking_keys=None):
    """항목 순서를 맞추고 빠진 항목은 null(또는 [])로 채운다. 빈 값도 null 로 통일한다.
    drop 에 있는 항목은 결과에서 뺀다."""
    result = {}
    for key, default in STORE_TEMPLATE.items():
        if key in drop:
            continue
        value = store.get(key, default)
        if value in ("", {}) or (value == [] and default is None):
            value = None
        result[key] = value if value is not None or default is None else default
    parking = store.get("parking") or {}
    result["parking"] = {key: (parking.get(key) or None) for key in (parking_keys or PARKING_TEMPLATE)}
    for key, value in store.items():  # 템플릿에 없는 항목도 유지
        if key not in result and key not in drop:
            result[key] = value
    return result


# 매장 형태: 스타벅스 매장 찾기의 '타입' 필터와 같다.
TYPE_CODES = {"T01": "DT", "T03": "리저브", "T27": "워크스루", "T12": "커뮤니티스토어"}


def store_type(name, detail, codes=None):
    """테마 코드로 판단하고, 코드가 없는 예전 데이터는 이름으로 판단한다.
    (이름 끝 R 이어도 삼성전자DSR·삼성디스플레이SDR 은 리저브가 아니고,
     양재역·별다방처럼 이름에 R 이 없는 리저브 매장도 있어 코드가 더 정확하다)"""
    if codes is None and "theme_codes" in (detail or {}):
        codes = set(detail["theme_codes"])
    if codes:
        return [label for code, label in TYPE_CODES.items() if code in codes]
    name = name or ""
    types = []
    if name.endswith("DT"):
        types.append("DT")
    if name.startswith("리저브") or (name.endswith("R") and not name.endswith(("DSR", "SDR"))):
        types.append("리저브")
    return types


# services 에서 뺄 항목: 주차는 parking 항목에 따로 있고, 블론드·피지오는 거의 모든 매장에 있어 구분 의미가 없다.
SERVICE_EXCLUDE = {"주차", "피지오", "블론드"}


def service_labels(detail, codes):
    """상세의 services·facilities 아이콘을 사이트에 보이는 순서대로 라벨로 바꾼다."""
    labels = []
    for url in (detail.get("services") or []) + (detail.get("facilities") or []):
        code = ICON_CODES.get(str(url).rsplit("/", 1)[-1])
        if code in SERVICE_LABELS:
            labels.append(SERVICE_LABELS[code])
    labels += [label for code, label in SERVICE_LABELS.items() if code in codes]  # 아이콘 없이 코드만 있는 경우
    return [label for label in dict.fromkeys(labels) if label not in SERVICE_EXCLUDE]


def normalize_starbucks(store):
    return normalize(store, STARBUCKS_DROP, STARBUCKS_PARKING)


def theme_codes(detail):
    if "theme_codes" in detail:
        return set(detail["theme_codes"])
    urls = detail.get("services", []) + detail.get("facilities", [])
    return {ICON_CODES[url.rsplit("/", 1)[-1]] for url in urls if url.rsplit("/", 1)[-1] in ICON_CODES}


def to_float(value):
    try:
        return round(float(value), 7)
    except (TypeError, ValueError):
        return None


def build_store(slug, location_item, detail):
    """자동 추출값으로 매장 1개를 만든다. (store, 검수 필요 항목) 반환"""
    review = []
    detail = detail or {}
    name = detail.get("name") or location_item.get("name", "")
    address = (detail.get("address") or location_item.get("address", "")).strip()
    description = detail.get("description", "")
    directions = detail.get("directions", "")
    # 매장 목록 API 코드(location)와 상세 코드를 합친다. 위생등급·리조트는 목록 코드에만 있다.
    list_codes = set(location_item.get("theme_codes") or [])
    codes = theme_codes(detail) | list_codes

    # 층: 주소·길안내·소개문을 모두 합친다. 주소에 일부 층만 적히고
    # 길안내에 전체 사용 층이 적힌 매장이 있어 첫 결과에서 멈추지 않는다.
    floors = []
    for source, parking_context in (
        (address, False),
        (directions, True),
        (description, True),
    ):
        for floor in parse_floors(source, parking_context=parking_context):
            if floor not in floors:
                floors.append(floor)
    if not parse_floors(address, parking_context=False) and floors:
        review.append("floors")

    # 뷰
    view = detect_view("\n".join(filter(None, (description, directions))), name)
    if view:
        review.append("view")

    # 펫: 스타벅스 펫존 아이콘 또는 소개문의 펫 프렌들리 언급, 그 외는 금지
    pet = "펫존" if "P02" in codes or re.search(r"펫\s*프렌들리|반려\s*동물과|펫과\s*함께", description) else "금지"

    # 주차
    parking = parse_starbucks_parking(detail.get("parking", "")) if detail else {}

    store = {
        "id": location_item.get("code") or detail.get("code"),
        "name": name,
        "address": address,
        "directions": clean_text(directions),
        "description": clean_text(description),
        "lat": to_float(location_item.get("latitude") or detail.get("latitude")),
        "lng": to_float(location_item.get("longitude") or detail.get("longitude")),
        "floors": sort_floors(floors),
        "location": [label for code, label in LOCATION_CODES.items() if code in codes],
        "type": store_type(name, detail, list_codes or None),
        "opened": location_item.get("open_dt") or None,
        "view": view,
        "smoking": "금지",
        "pet": pet,
        "phone": (detail.get("phone") or "").strip() or None,
        "services": service_labels(detail, codes),
        "parking": parking,
        "images": list(dict.fromkeys(detail.get("images") or [])),
    }
    if not detail:
        review.append("details")
    return store, review


def main():
    if (STORES_DIR / "stores.json").exists() and "--force" not in sys.argv:
        print("starbucks/stores.json 이 이미 있습니다. 직접 수정한 내용이 덮어써지므로 실행하지 않습니다.\n"
              "전체를 다시 생성하려면: python starbucks-stores.py --force", flush=True)
        return
    today = datetime.now(ZoneInfo("Asia/Seoul")).strftime("%Y-%m-%d")
    previous = load_json(STORES_DIR / "stores.json", {}) or {}
    backup = load_json(STORES_DIR / "stores-back.json", {}) or {}
    previous_items = {item["id"]: item for item in previous.get("item", [])}
    # stores-back.json은 재생성 전 수동 조사값의 안전망이다.
    for item in backup.get("item", []):
        current = previous_items.setdefault(item["id"], item)
        for field in ("seats", "study"):
            if current.get(field) is None and item.get(field) is not None:
                current[field] = item[field]
        for field in ("floors", "view"):
            if not current.get(field) and item.get(field):
                current[field] = item[field]

    stores = {}
    review = {}
    region_of = {}  # 정렬용 (결과에는 넣지 않음)
    missing_regions = []
    for slug in REGIONS:
        location = load_json(DATA_DIR / "location" / slug / "latest.json")
        if location is None:
            missing_regions.append(slug)
            print(f"위치 데이터 없음: {slug}", flush=True)
            continue
        details = load_json(DATA_DIR / "details" / slug / "latest.json", {}) or {}
        detail_by_code = {item["code"]: item for item in details.get("item", [])}

        for location_item in location.get("item", []):
            store, fields = build_store(slug, location_item, detail_by_code.get(location_item["code"]))
            # 자동 수집으로 얻기 어려워 사람이 조사해 넣은 값은 재생성해도 보존한다.
            old = previous_items.get(store["id"], {})
            for field in ("seats", "study"):
                if old.get(field) is not None:
                    store[field] = old[field]
            for field in ("floors", "view"):
                if old.get(field):
                    store[field] = old[field]
            stores[store["id"]] = store
            region_of[store["id"]] = slug
            if fields:
                review[store["id"]] = {"name": store["name"], "fields": fields}

    # 목록에서 사라진 매장은 결과에서도 뺀다.
    # 단, 읽지 못한 지역이 있으면 그 지역 매장이 빠지지 않도록 이전 값을 그대로 둔다.
    for store_id, item in previous_items.items():
        if store_id not in stores:
            if missing_regions:
                stores[store_id] = item
            else:
                print(f"목록에서 사라진 매장: {item.get('name')} ({store_id})", flush=True)

    region_order = {slug: index for index, slug in enumerate(REGIONS)}
    items = sorted(
        (normalize_starbucks(item) for item in stores.values()),
        key=lambda item: (region_order.get(region_of.get(item["id"]), 99), item.get("name") or ""),
    )
    for item in items:
        item.pop("region", None)

    def payload(rows):
        return {
            "kind": "Korea Starbucks Stores",
            "date": today,
            "count": len(rows),
            "item": rows,
        }

    write_json(STORES_DIR / "stores.json", payload(items))
    fields = {}
    for info in review.values():
        for field in info["fields"]:
            fields[field] = fields.get(field, 0) + 1
    summary = ", ".join(f"{field} {count}" for field, count in fields.items()) or "없음"
    print(f"매장 데이터 생성 완료: {len(items)}개 / 검수 필요: {summary}", flush=True)


if __name__ == "__main__":
    main()
