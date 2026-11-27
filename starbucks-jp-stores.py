"""일본 스타벅스 매장 정보 수집기 (GitHub Actions 에서 매주 월요일 실행).

일본 공식 매장 사이트의 사이트맵에서 매장 상세 페이지(detail-<id>/) 주소를 모으고,
각 페이지의 JSON-LD(Restaurant 스키마 + BreadcrumbList)만 읽어 매장 정보를 만든다.
후기·리뷰는 수집하지 않는다.

출력
- starbucks-jp/stores.json : 전체 매장 스냅샷 (한국 starbucks/stores.json 과 같은 키 구성)

한국 파일과 같은 톱레벨 키(kind, date, count, item)와 매장 레코드 키(id, name, address,
lat, lng, phone)를 쓰고, 일본에만 있는 값(도도부현, 영업시간, 공식 페이지 주소 등)은
추가 키로 넣는다. 직전 파일과 비교하지 않고 매번 전체를 새로 덮어쓴다.

robots.txt 는 `User-Agent: *` 에 Disallow 없이 사이트맵만 안내한다(2026-11 확인).
요청은 순차로 보내고 간격을 두며, 자동 수집기임을 User-Agent 에 밝힌다.

실행
- python starbucks-jp-stores.py                : 전체 수집 (약 2,140건, 30분 안팎)
- python starbucks-jp-stores.py --limit 10     : 앞에서 10건만 수집 (시험용 → stores.sample.json)
- python starbucks-jp-stores.py --ids 1,2,3    : 지정한 매장만 수집 (시험용 → stores.sample.json)
- python starbucks-jp-stores.py --no-write     : 파일을 쓰지 않고 결과만 확인
"""

from argparse import ArgumentParser
from datetime import datetime
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo
import json
import re
import time

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "starbucks-jp"
STORES_PATH = DATA_DIR / "stores.json"
SITEMAP_URL = "https://store.starbucks.co.jp/sitemap.xml"
DETAIL_PATTERN = re.compile(r"https://store\.starbucks\.co\.jp/(detail-\d+)/")
DETAIL_ID = re.compile(r"detail-(\d+)")
USER_AGENT = "StarbucksJPStoreCollector/1.0 (+https://github.com/websszoa/data-starbucks)"
REQUEST_TIMEOUT = 25
REQUEST_INTERVAL = 0.35
MAX_RETRIES = 3

WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday", "PublicHolidays"]

# 도도부현: JIS 코드 순서, 로마자 슬러그, 일본어 이름, 한국어 이름
PREFECTURES = [
    ("01", "hokkaido", "北海道", "홋카이도"),
    ("02", "aomori", "青森県", "아오모리"),
    ("03", "iwate", "岩手県", "이와테"),
    ("04", "miyagi", "宮城県", "미야기"),
    ("05", "akita", "秋田県", "아키타"),
    ("06", "yamagata", "山形県", "야마가타"),
    ("07", "fukushima", "福島県", "후쿠시마"),
    ("08", "ibaraki", "茨城県", "이바라키"),
    ("09", "tochigi", "栃木県", "도치기"),
    ("10", "gunma", "群馬県", "군마"),
    ("11", "saitama", "埼玉県", "사이타마"),
    ("12", "chiba", "千葉県", "지바"),
    ("13", "tokyo", "東京都", "도쿄"),
    ("14", "kanagawa", "神奈川県", "가나가와"),
    ("15", "niigata", "新潟県", "니가타"),
    ("16", "toyama", "富山県", "도야마"),
    ("17", "ishikawa", "石川県", "이시카와"),
    ("18", "fukui", "福井県", "후쿠이"),
    ("19", "yamanashi", "山梨県", "야마나시"),
    ("20", "nagano", "長野県", "나가노"),
    ("21", "gifu", "岐阜県", "기후"),
    ("22", "shizuoka", "静岡県", "시즈오카"),
    ("23", "aichi", "愛知県", "아이치"),
    ("24", "mie", "三重県", "미에"),
    ("25", "shiga", "滋賀県", "시가"),
    ("26", "kyoto", "京都府", "교토"),
    ("27", "osaka", "大阪府", "오사카"),
    ("28", "hyogo", "兵庫県", "효고"),
    ("29", "nara", "奈良県", "나라"),
    ("30", "wakayama", "和歌山県", "와카야마"),
    ("31", "tottori", "鳥取県", "돗토리"),
    ("32", "shimane", "島根県", "시마네"),
    ("33", "okayama", "岡山県", "오카야마"),
    ("34", "hiroshima", "広島県", "히로시마"),
    ("35", "yamaguchi", "山口県", "야마구치"),
    ("36", "tokushima", "徳島県", "도쿠시마"),
    ("37", "kagawa", "香川県", "가가와"),
    ("38", "ehime", "愛媛県", "에히메"),
    ("39", "kochi", "高知県", "고치"),
    ("40", "fukuoka", "福岡県", "후쿠오카"),
    ("41", "saga", "佐賀県", "사가"),
    ("42", "nagasaki", "長崎県", "나가사키"),
    ("43", "kumamoto", "熊本県", "구마모토"),
    ("44", "oita", "大分県", "오이타"),
    ("45", "miyazaki", "宮崎県", "미야자키"),
    ("46", "kagoshima", "鹿児島県", "가고시마"),
    ("47", "okinawa", "沖縄県", "오키나와"),
]
PREFECTURE_BY_NAME = {ja: (code, slug, ko) for code, slug, ja, ko in PREFECTURES}


# ── 요청 ──────────────────────────────────────────────────────────
def fetch(url, interval=REQUEST_INTERVAL):
    """URL 을 한 번(필요하면 재시도) 가져온다. 404/410 은 그대로 올려보낸다."""
    request = Request(url, headers={"User-Agent": USER_AGENT, "Accept-Language": "ja,en;q=0.8"})
    for attempt in range(1, MAX_RETRIES + 1):
        time.sleep(interval)
        try:
            with urlopen(request, timeout=REQUEST_TIMEOUT) as response:
                return response.read().decode("utf-8", errors="replace")
        except HTTPError as error:
            if error.code in (404, 410):
                raise
            if attempt == MAX_RETRIES:
                raise RuntimeError(f"요청 실패: {url} ({error})") from error
            time.sleep(60 if error.code == 429 else attempt * 5)
        except (URLError, TimeoutError) as error:
            if attempt == MAX_RETRIES:
                raise RuntimeError(f"요청 실패: {url} ({error})") from error
            time.sleep(attempt * 5)


def store_number(url):
    match = DETAIL_ID.search(url)
    return int(match.group(1)) if match else 0


def get_detail_urls():
    xml = fetch(SITEMAP_URL, interval=0)
    paths = sorted(set(DETAIL_PATTERN.findall(xml)), key=store_number)
    urls = [f"https://store.starbucks.co.jp/{path}/" for path in paths]
    if len(urls) < 1000:
        raise RuntimeError(f"사이트맵의 매장 상세 URL 이 {len(urls)}건뿐입니다. 사이트맵 구조가 바뀌었는지 확인하세요.")
    return urls


# ── 상세(JSON-LD) ────────────────────────────────────────────────
def json_ld_blocks(html):
    return re.findall(r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', html, re.S)


def parse_detail(html, url):
    """JSON-LD 에서 매장 레코드를 만든다. Restaurant 스키마가 없으면 예외."""
    restaurant = None
    crumb_region = ""
    for block in json_ld_blocks(html):
        try:
            data = json.loads(block)
        except json.JSONDecodeError:
            continue
        for item in data if isinstance(data, list) else [data]:
            if not isinstance(item, dict):
                continue
            kind = item.get("@type")
            kinds = kind if isinstance(kind, list) else [kind]
            if "Restaurant" in kinds:
                restaurant = item
            elif "BreadcrumbList" in kinds:
                for element in item.get("itemListElement", []):
                    if isinstance(element, dict) and element.get("position") == 3:
                        # 3번째가 도도부현, 4번째가 시구정촌이다.
                        crumb_region = str((element.get("item") or {}).get("name", "")).strip()

    if restaurant is None:
        raise ValueError("Restaurant JSON-LD 없음")

    name = str(restaurant.get("name", "")).strip()
    if not name:
        raise ValueError("매장 이름 없음")

    address = restaurant.get("address") or {}
    if not isinstance(address, dict):  # 주소가 문자열로만 오는 경우
        address = {}
    region = str(address.get("addressRegion", "")).strip() or crumb_region
    city = str(address.get("addressLocality", "")).strip()
    street = str(address.get("streetAddress", "")).strip()
    postal = str(address.get("postalCode", "")).strip()

    geo = restaurant.get("geo") or {}
    latitude = as_float(geo.get("latitude"))
    longitude = as_float(geo.get("longitude"))
    if latitude is None or longitude is None:
        raise ValueError("좌표 없음")

    code, slug = "", ""
    if region in PREFECTURE_BY_NAME:
        code, slug, _ = PREFECTURE_BY_NAME[region]

    return {
        "id": str(store_number(url)),
        "name": name,
        "address": "".join(part for part in (region, city, street) if part),
        "lat": latitude,
        "lng": longitude,
        "phone": str(restaurant.get("telephone", "")).strip(),
        "prefecture": region,
        "prefecture_slug": slug,
        "prefecture_code": code,
        "city": city,
        "postal_code": postal,
        "opening_hours": parse_hours(restaurant.get("openingHoursSpecification")),
        "url": f"https://store.starbucks.co.jp/detail-{store_number(url)}/",
    }


def parse_hours(specs):
    if not specs:
        return []
    if isinstance(specs, dict):
        specs = [specs]
    hours = []
    for spec in specs:
        if not isinstance(spec, dict):
            continue
        days = spec.get("dayOfWeek") or []
        if isinstance(days, str):
            days = re.split(r"[\s,]+", days.strip())
        days = [weekday_name(day) for day in days if day]
        hours.append({
            "days": [day for day in WEEKDAYS if day in days],
            "opens": str(spec.get("opens", "")).strip(),
            "closes": str(spec.get("closes", "")).strip(),
        })
    return hours


def weekday_name(value):
    """schema.org 는 요일을 URL 로도 쓰므로 마지막 조각만 남긴다."""
    return str(value).rstrip("/").rsplit("/", 1)[-1]


def as_float(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


# ── 저장 ──────────────────────────────────────────────────────────
def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as file:
        json.dump(data, file, ensure_ascii=False, indent=4)
        file.write("\n")


def collect(urls, interval):
    items, errors, gone = [], [], []
    started = time.time()
    for index, url in enumerate(urls, start=1):
        try:
            items.append(parse_detail(fetch(url, interval=interval), url))
        except HTTPError as error:
            # 사이트맵에 남아 있지만 실제 페이지가 없는 매장(폐점 등)은 따로 센다.
            target = gone if error.code in (404, 410) else errors
            target.append({"id": str(store_number(url)), "url": url, "error": f"HTTP {error.code}"})
        except Exception as error:
            errors.append({"id": str(store_number(url)), "url": url, "error": str(error)})
        if index % 25 == 0 or index == len(urls):
            elapsed = time.time() - started
            print(
                f"수집: {index}/{len(urls)} (성공 {len(items)}, 실패 {len(errors)}, 페이지 없음 {len(gone)}, {elapsed:.0f}초)",
                flush=True,
            )
    return items, errors, gone


def main():
    parser = ArgumentParser(description="일본 스타벅스 매장 정보 수집기")
    parser.add_argument("--limit", type=int, help="앞에서 N건만 수집 (시험용, stores.sample.json 에 저장)")
    parser.add_argument("--ids", help="쉼표로 구분한 매장 id 만 수집 (시험용, stores.sample.json 에 저장)")
    parser.add_argument("--no-write", action="store_true", help="파일을 쓰지 않는다")
    parser.add_argument("--interval", type=float, default=REQUEST_INTERVAL, help="요청 간격(초)")
    args = parser.parse_args()

    today = datetime.now(ZoneInfo("Asia/Seoul")).strftime("%Y-%m-%d")
    print(f"일본 스타벅스 수집 기준일: {today}", flush=True)

    urls = get_detail_urls()
    print(f"사이트맵에서 매장 상세 URL {len(urls)}건을 찾았습니다.", flush=True)

    partial = False
    if args.ids:
        wanted = {value.strip() for value in args.ids.split(",") if value.strip()}
        urls = [url for url in urls if str(store_number(url)) in wanted]
        partial = True
        if not urls:
            raise SystemExit("--ids 에 해당하는 매장이 사이트맵에 없습니다.")
    elif args.limit:
        urls = urls[: args.limit]
        partial = True
    output_path = DATA_DIR / "stores.sample.json" if partial else STORES_PATH
    if partial:
        print(f"시험 실행: {len(urls)}건만 수집해 {output_path.name} 에 저장합니다. (stores.json 은 건드리지 않음)", flush=True)

    items, errors, gone = collect(urls, args.interval)
    items.sort(key=lambda item: int(item["id"]))
    print(f"\n수집 결과: 성공 {len(items)} / 실패 {len(errors)} / 페이지 없음 {len(gone)}", flush=True)

    unknown = sorted({item["prefecture"] or "(알 수 없음)" for item in items if not item["prefecture_slug"]})
    if unknown:
        print(f"도도부현을 알아내지 못한 매장이 있습니다: {', '.join(unknown)}", flush=True)

    if args.no_write:
        if items:
            print(json.dumps(items[0], ensure_ascii=False, indent=2), flush=True)
        return

    data = {
        "kind": "Japan Starbucks Stores",
        "date": today,
        "count": len(items),
        "item": items,
    }
    write_json(output_path, data)
    print(f"저장: {output_path.relative_to(ROOT)} ({len(items)}건)", flush=True)

    if errors or gone:
        for row in (errors + gone)[:10]:
            print(f"  - {row['id']}: {row['error']}", flush=True)
        raise SystemExit(f"{len(errors) + len(gone)}건을 읽지 못했습니다. 위 목록과 실행 로그를 확인하세요.")


if __name__ == "__main__":
    main()
