"""서울 스타벅스 매장의 네이버 블로그 후기를 매장별 원문(raw)으로 모아 저장한다.

네이버 검색 Open API 의 블로그 검색(openapi.naver.com/v1/search/blog.json)을 매장명으로 질의한다.
키는 환경변수 NAVER_CLIENT_ID / NAVER_CLIENT_SECRET 에서 읽는다. 키가 없으면 아무것도 저장하지 않고
"수집 생략" 메시지만 남기고 끝난다(워크플로가 실패하지 않도록 종료 코드 0).

실행: python starbucks-reviews.py
시험: python starbucks-reviews.py --store-id 9861 --fixture tests/fixtures/naver --out-dir /tmp/reviews

입력
- starbucks/stores.json                      : 주소가 서울인 매장만 대상 (시/도 항목이 없어 주소로 판별)
- 환경변수 NAVER_CLIENT_ID, NAVER_CLIENT_SECRET : 네이버 검색 Open API 키
- 환경변수 REVIEWS_MAX_STORES                : 실행당 매장 상한 (기본 600)
- starbucks/reviews/raw/seoul/latest.json    : 이전 실행 결과 (새 글 판별 기준)

출력
- starbucks/reviews/raw/seoul/{YYYY-MM-DD}.json : 매일 실행 결과
- starbucks/reviews/raw/seoul/latest.json       : 위 파일과 같은 내용 (고정 주소용)

이 파일은 원문 수집 단계다. 다음 단계로 이 원문을 매장별 pros/cons/ratings 로 요약해
starbucks/reviews.json(요약본)을 갱신하는 스크립트를 따로 붙인다. (요약 단계는 아직 없음)
"""

from datetime import datetime
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo
import argparse
import html
import json
import os
import random
import re
import time

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "starbucks"
STORES_PATH = DATA_DIR / "stores.json"
OUT_DIR = DATA_DIR / "reviews" / "raw" / "seoul"

API_URL = "https://openapi.naver.com/v1/search/blog.json"
SEOUL_PREFIX = "서울"

DEFAULT_MAX_STORES = 600        # 실행당 매장 상한 (REVIEWS_MAX_STORES 로 조정)
DISPLAY = 100                   # 질의당 결과 수 (네이버 최대 100)
MAX_START = 1000                # 네이버 start 최대값 (지금은 첫 페이지만 쓴다)
QUERY_SLEEP = (0.2, 0.5)        # 호출 사이 대기 (초)
RETRIES = 2                     # 429/5xx 재시도 횟수
SPAM_SITE_REPEAT = 4            # 같은 블로그(도메인+아이디) 글이 매장당 이만큼이면 도배로 표시
PREV_FILES = 7                  # latest.json 에 없는 매장을 찾을 때 되짚어 볼 최근 파일 수

AD_PATTERN = re.compile(r"체험단|협찬|서포터즈|광고|원고료|제공\s*받|업체\s*(?:에서|로부터)?\s*제공|대가성|소정의")


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


def clean_text(value):
    """네이버가 넣어 주는 <b> 강조 태그와 HTML 엔티티를 걷어낸다."""
    text = re.sub(r"<[^>]*>", "", value or "")
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def norm(value):
    """비교용: 공백·특수문자·대소문자를 무시한다."""
    return re.sub(r"[^0-9a-z가-힣]", "", (value or "").lower())


# ── 매장명 → 질의·필터 키 ─────────────────────────────────────────
SUFFIX = re.compile(r"\s*(?:DT|B\d{1,2}F|\d{1,2}층)\s*$", re.I)


def core_name(name):
    """'안양호계DT', 'KT광화문웨스트B1F', '잠실R' 같은 접미사를 뺀 매장 핵심 이름."""
    text = re.sub(r"\s+", " ", (name or "")).strip()
    text = SUFFIX.sub("", text).strip()
    text = re.sub(r"(?<=[가-힣])R$", "", text).strip()
    return text or (name or "").strip()


def match_keys(name):
    """제목·요약에 이 중 하나라도 있으면 그 매장 글 후보로 본다 (공백·특수문자 무시)."""
    core = core_name(name)
    keys = []
    full = norm(core)
    if len(full) >= 2:
        keys.append(full)
    for token in re.split(r"[\s·・,()\[\]/]+", core):
        key = norm(token)
        # 한글은 2자부터, 영문·숫자는 3자부터 (예: '63' 같은 숫자만으로는 잡지 않는다)
        if len(key) < 2 or key in keys:
            continue
        if re.search(r"[가-힣]", key) or len(key) >= 3:
            keys.append(key)
    return keys


def district(address):
    """주소에서 구 이름을 뽑는다 (동명 매장 혼입을 줄이는 두 번째 질의에 쓴다)."""
    for token in (address or "").split():
        if token.endswith(("구", "군")):
            return token
    return ""


def build_queries(store):
    """질의는 매장명 기준. 이름이 짧아 헷갈릴 때만 구 이름을 덧붙여 한 번 더 묻는다."""
    core = core_name(store.get("name"))
    queries = [f"스타벅스 {core}"]
    if len(norm(core)) <= 3:
        gu = district(store.get("address"))
        if gu:
            queries.append(f"스타벅스 {core} {gu}")
    return queries


def matches(title, description, keys):
    text = norm(title) + "\n" + norm(description)
    return any(key in text for key in keys)


def site_key(link):
    """도배 판정용 사이트 키. 네이버 블로그는 도메인만으로는 모두 같아서 아이디까지 붙인다."""
    parsed = urlparse(link)
    host = (parsed.netloc or "").lower()
    if host.startswith("www."):
        host = host[4:]
    parts = [part for part in parsed.path.split("/") if part]
    if "blog.naver.com" in host and parts:
        return f"{host}/{parts[0]}"
    return host


# ── 네이버 블로그 검색 ────────────────────────────────────────────
class AuthError(Exception):
    """키가 잘못된 경우 (재시도해도 결과가 같으므로 실행 전체를 멈춘다)."""


def search(query, client_id, client_secret, start=1):
    start = max(1, min(int(start), MAX_START))
    params = urlencode({"query": query, "display": DISPLAY, "start": start, "sort": "sim"})
    request = Request(
        f"{API_URL}?{params}",
        headers={"X-Naver-Client-Id": client_id, "X-Naver-Client-Secret": client_secret},
    )
    for attempt in range(RETRIES + 1):
        try:
            with urlopen(request, timeout=10) as response:
                return json.loads(response.read().decode("utf-8"))
        except HTTPError as error:
            if error.code in (401, 403):
                raise AuthError(f"네이버 API 키가 거부되었습니다 (HTTP {error.code})")
            if (error.code == 429 or 500 <= error.code < 600) and attempt < RETRIES:
                time.sleep(0.5 * (2 ** attempt))
                continue
            raise
        except URLError:
            if attempt < RETRIES:
                time.sleep(0.5 * (2 ** attempt))
                continue
            raise
    return {}


def fixture_search(fixture_dir, store_id):
    """시험용: 저장된 네이버 응답(fixture)을 그 질의의 응답으로 쓴다."""
    return load_json(Path(fixture_dir) / f"{store_id}.json", {}) or {}


# ── 이전 결과 ─────────────────────────────────────────────────────
def load_previous(out_dir, current_date):
    """매장 id → 이전에 모았던 글 링크 집합. 없으면 그 매장은 첫 수집이다."""
    previous = {}
    latest = load_json(out_dir / "latest.json", {}) or {}
    for store_id, info in (latest.get("item") or {}).items():
        previous[store_id] = {post.get("link") for post in info.get("posts") or [] if post.get("link")}
    # 실행당 상한(회전 수집) 때문에 latest.json 에 없는 매장은 최근 날짜 파일에서 찾는다.
    files = sorted(
        (path for path in out_dir.glob("*.json") if re.fullmatch(r"\d{4}-\d{2}-\d{2}\.json", path.name)),
        reverse=True,
    )[:PREV_FILES]
    for path in files:
        data = load_json(path, {}) or {}
        if data.get("date", "") >= current_date:
            continue
        for store_id, info in (data.get("item") or {}).items():
            if store_id in previous:
                continue
            previous[store_id] = {post.get("link") for post in info.get("posts") or [] if post.get("link")}
    return previous


def to_iso_date(value):
    """네이버 postdate '20261120' → '2026-11-20'."""
    text = clean_text(value)
    if re.fullmatch(r"\d{8}", text):
        return f"{text[:4]}-{text[4:6]}-{text[6:]}"
    return text


def mark_flags(posts):
    """광고·도배는 버리지 않고 표시만 한다."""
    counts = {}
    for post in posts:
        key = site_key(post["link"])
        counts[key] = counts.get(key, 0) + 1
    for post in posts:
        flags = []
        if AD_PATTERN.search(f"{post['title']} {post['description']}"):
            flags.append("광고")
        if counts.get(site_key(post["link"]), 0) >= SPAM_SITE_REPEAT:
            flags.append("도배")
        post["flag"] = flags
    return posts


def collect_store(store, client_id, client_secret, fixture_dir):
    """매장 하나를 질의해 글 목록을 만든다. (queries, posts, errors) 반환"""
    queries = build_queries(store)
    keys = match_keys(store.get("name"))
    posts, seen, errors = [], set(), []

    for query in queries:
        try:
            if fixture_dir:
                data = fixture_search(fixture_dir, store.get("id"))
            else:
                data = search(query, client_id, client_secret)
        except AuthError:
            raise
        except Exception as error:  # 재시도 후에도 실패한 질의는 건너뛴다.
            errors.append(f"{query}: {error}")
            continue

        for row in data.get("items") or []:
            link = (row.get("link") or "").strip()
            title = clean_text(row.get("title"))
            description = clean_text(row.get("description"))
            if not link or link in seen or not matches(title, description, keys):
                continue
            seen.add(link)
            posts.append({
                "title": title,
                "link": link,
                "description": description,
                "bloggername": clean_text(row.get("bloggername")),
                "postdate": to_iso_date(row.get("postdate")),
            })
        if not fixture_dir:
            time.sleep(random.uniform(*QUERY_SLEEP))

    mark_flags(posts)
    posts.sort(key=lambda post: post["postdate"], reverse=True)
    return queries, posts, errors


def select_stores(seoul, args, current_date):
    """대상 매장을 고른다. 상한에 걸리면 날짜에 따라 시작 위치를 옮겨 며칠에 걸쳐 전부 훑는다."""
    if args.store_id:
        wanted = list(dict.fromkeys(args.store_id))
        by_id = {store["id"]: store for store in seoul}
        targets = [by_id[store_id] for store_id in wanted if store_id in by_id]
        missing = [store_id for store_id in wanted if store_id not in by_id]
        if missing:
            print(f"서울 매장 목록에 없는 id 입니다: {', '.join(missing)}", flush=True)
        return targets, 0

    limit = args.limit or int(os.environ.get("REVIEWS_MAX_STORES") or DEFAULT_MAX_STORES)
    offset = args.offset
    if offset is None:
        offset = (datetime.strptime(current_date, "%Y-%m-%d").toordinal() * limit % len(seoul)) if len(seoul) > limit else 0
    offset %= max(len(seoul), 1)
    rotated = seoul[offset:] + seoul[:offset]
    return rotated[:limit], offset


def main(argv=None):
    parser = argparse.ArgumentParser(description="서울 스타벅스 매장의 네이버 블로그 후기를 모은다.")
    parser.add_argument("--limit", type=int, help="이번 실행에서 볼 매장 수 (기본 REVIEWS_MAX_STORES 또는 600)")
    parser.add_argument("--offset", type=int, help="매장 목록에서 시작 위치 (기본: 날짜로 자동 회전)")
    parser.add_argument("--store-id", action="append", help="이 매장 id 만 수집 (여러 번 쓸 수 있음)")
    parser.add_argument("--date", help="기준일 YYYY-MM-DD (기본: 한국 시간 오늘)")
    parser.add_argument("--out-dir", type=Path, default=OUT_DIR, help="출력 폴더 (시험용)")
    parser.add_argument("--fixture", type=Path, help="네이버 응답 대신 쓸 fixture 폴더 ({매장id}.json)")
    args = parser.parse_args(argv)

    client_id = (os.environ.get("NAVER_CLIENT_ID") or "").strip()
    client_secret = (os.environ.get("NAVER_CLIENT_SECRET") or "").strip()
    if not args.fixture and not (client_id and client_secret):
        print("NAVER_CLIENT_ID / NAVER_CLIENT_SECRET 환경변수가 없어 수집을 생략합니다. "
              "키를 설정한 뒤 다시 실행하세요.", flush=True)
        return 0

    current_date = args.date or datetime.now(ZoneInfo("Asia/Seoul")).strftime("%Y-%m-%d")
    stores = (load_json(STORES_PATH, {}) or {}).get("item") or []
    seoul = [store for store in stores if (store.get("address") or "").startswith(SEOUL_PREFIX)]
    if not seoul:
        print("starbucks/stores.json 에서 서울 매장을 찾지 못했습니다.", flush=True)
        return 0

    targets, offset = select_stores(seoul, args, current_date)
    previous = load_previous(args.out_dir, current_date)
    print(f"기준일 {current_date} / 서울 매장 {len(seoul)}곳 중 {len(targets)}곳 수집 "
          f"(시작 위치 {offset})", flush=True)

    item, failures, queries_used, total_posts, total_new = {}, [], 0, 0, 0
    for position, store in enumerate(targets, start=1):
        try:
            queries, posts, errors = collect_store(store, client_id, client_secret, args.fixture)
        except AuthError as error:
            print(f"{error} — 키를 확인한 뒤 다시 실행하세요.", flush=True)
            return 0
        queries_used += len(queries)
        if errors and len(errors) == len(queries):  # 모든 질의 실패 → 저장하지 않고 다음 매장으로
            failures.append((store, errors))
            print(f"[{position}/{len(targets)}] {store.get('name')} 실패: {errors[0]}", flush=True)
            continue

        known = previous.get(store["id"])
        for post in posts:
            post["new"] = True if known is None else post["link"] not in known
        new_count = sum(1 for post in posts if post["new"])
        item[store["id"]] = {
            "name": store.get("name"),
            "address": store.get("address"),
            "query": " / ".join(queries),
            "post_count": len(posts),
            "new_count": new_count,
            "posts": posts,
        }
        total_posts += len(posts)
        total_new += new_count
        if position % 50 == 0 or position == len(targets):
            print(f"[{position}/{len(targets)}] {store.get('name')}: {len(posts)}건 (새 글 {new_count}건) "
                  f"— 누적 {total_posts}건", flush=True)

    if not item:
        print("저장할 결과가 없습니다. (모든 매장 수집 실패)", flush=True)
        return 0

    payload = {
        "kind": "Korea Starbucks Reviews Raw",
        "date": current_date,
        "count": len(item),
        "item": item,
    }
    write_json(args.out_dir / f"{current_date}.json", payload)
    write_json(args.out_dir / "latest.json", payload)

    print(f"\n저장 완료: {args.out_dir / f'{current_date}.json'} (+ latest.json)", flush=True)
    print(f"매장 {len(item)}곳 / 글 {total_posts}건 (새 글 {total_new}건) / 질의 {queries_used}회", flush=True)
    ad = sum(1 for info in item.values() for post in info["posts"] if "광고" in post["flag"])
    spam = sum(1 for info in item.values() for post in info["posts"] if "도배" in post["flag"])
    print(f"표시만 한 글: 광고 {ad}건, 도배 {spam}건", flush=True)
    if failures:
        names = ", ".join(store.get("name") or store.get("id") for store, _ in failures)
        print(f"수집 실패 {len(failures)}곳 (저장에서 제외): {names}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
