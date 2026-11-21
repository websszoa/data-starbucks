"""투썸플레이스 공식 매장 찾기 API로 전국 매장 데이터를 수집한다.

실행:
    python3 twosome-stores.py
    python3 twosome-stores.py --delay 0.2

출력:
    twosome/stores.json
    twosome/latest.json
    twosome/{year}/twosome_{date}.json
"""

from argparse import ArgumentParser
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from http.cookiejar import CookieJar
from pathlib import Path
from time import sleep
from urllib.parse import urlencode
from urllib.request import HTTPCookieProcessor, Request, build_opener, urlopen
from zoneinfo import ZoneInfo
import json
import re


ROOT = Path(__file__).resolve().parent
OUTPUT_DIR = ROOT / "twosome"
BASE_URL = "https://mo.twosome.co.kr"
LIST_PAGE = "/ts/tsSearchStoreList.do"
LIST_API = "/ts/storeSearchListAjax.json"
DETAIL_API = "/ts/storeDetailInfoAjax.json"
SOURCE_URL = BASE_URL + LIST_PAGE

SERVICE_NAMES = {
    "01": "투썸오더",
    "02": "드라이브스루",
    "04": "디카페인",
    "05": "와이파이",
    "07": "주차",
    "09": "커뮤니티룸",
    "11": "드라이브인",
    "13": "하트",
    "16": "우유 선택",
    "17": "식물성 음료",
}

OUTPUT_FIELDS = (
    "id", "name", "address", "lat", "lng", "services",
    "directions", "parking_raw", "images", "opened",
)


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(data, ensure_ascii=False, indent=2)
    # 서비스처럼 짧은 문자열 배열은 읽기 쉽게 한 줄로 유지한다.
    text = re.sub(
        r"\[\n((?:\s+\"(?:[^\"\\]|\\.)*\",?\n)+)\s*\]",
        lambda match: "[" + ", ".join(
            line.strip().rstrip(",") for line in match.group(1).splitlines()
        ) + "]",
        text,
    )
    with path.open("w", encoding="utf-8") as file:
        file.write(text + "\n")


def to_float(value):
    try:
        return round(float(value), 7)
    except (TypeError, ValueError):
        return None


def clean_text(value):
    return re.sub(r"\s+", " ", value or "").strip()


def parse_options(raw):
    try:
        options = json.loads(raw or "[]")
    except (TypeError, json.JSONDecodeError):
        options = []
    codes = [str(option.get("CD", "")) for option in options]
    names = []
    for option in options:
        code = str(option.get("CD", ""))
        name = SERVICE_NAMES.get(code) or clean_text(option.get("NM"))
        if name and name not in names:
            names.append(name)
    return codes, names


def store_image(value):
    if not value:
        return None
    value = re.sub(r"([?&])width=\d+", r"\1width=720", value)
    value = re.sub(r"([?&])height=\d+", r"\1height=720", value)
    return value if value.startswith("http") else BASE_URL + value


def image_list(value):
    images = []
    for item in (value or "").split(","):
        image = store_image(item.strip())
        if image and image not in images:
            images.append(image)
    return images


def build_store(row):
    _, services = parse_options(row.get("OPT_CD_JSON"))
    address = clean_text(" ".join(filter(None, (
        row.get("STORE_ROADNM_BASIC_AD"),
        row.get("STORE_ROADNM_DTL_AD"),
    ))))
    store = {
        "id": str(row.get("STORE_CD") or ""),
        "name": clean_text(row.get("STORE_NM")),
        "address": address,
        "lat": to_float(row.get("LAT")),
        "lng": to_float(row.get("LONGI")),
        "services": services,
        "directions": clean_text(row.get("DIR_CNTNT")) or None,
        "parking_raw": clean_text(row.get("PARK_INFO_CNTNT")) or None,
        "images": row.get("_IMAGES") or image_list(row.get("STORE_IMG_01") or row.get("STORE_IMG")),
        "opened": row.get("SOB_DT") or None,
    }
    return {key: value for key, value in store.items() if value is not None}


def compact_existing(data):
    """기존 상세 형식 JSON을 현재의 간결한 공개 형식으로 바꾼다."""
    items = []
    for item in data.get("item", []):
        compact = {key: item[key] for key in OUTPUT_FIELDS if key in item and item[key] is not None}
        if "images" not in compact and item.get("image"):
            compact["images"] = [item["image"]]
        items.append(compact)
    data["item"] = items
    data["count"] = len(data["item"])
    return data


class Collector:
    def __init__(self, timeout=30):
        self.timeout = timeout
        self.opener = build_opener(HTTPCookieProcessor(CookieJar()))
        self.headers = {
            "User-Agent": "Mozilla/5.0 (compatible; Korea-store-data-collector/1.0)",
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "Referer": SOURCE_URL,
            "X-Requested-With": "XMLHttpRequest",
        }

    def initialize(self):
        request = Request(SOURCE_URL, headers={"User-Agent": self.headers["User-Agent"]})
        with self.opener.open(request, timeout=self.timeout) as response:
            response.read(1)

    def get_page(self, page_number):
        form = urlencode({
            "searchTxt": "",
            "lat": "37.5665",
            "longi": "126.9780",
            "scope": "all",
            "pageNum": str(page_number),
            "optCds": "",
            "menuCd": "",
            "reqPath": LIST_PAGE,
            "menuType": "CD",
            "menuGubn": "",
        }).encode()
        request = Request(BASE_URL + LIST_API, data=form, headers=self.headers, method="POST")
        with self.opener.open(request, timeout=self.timeout) as response:
            data = json.load(response)
        if str(data.get("queryCode")) != "1000":
            raise RuntimeError(data.get("queryMessage") or f"API 오류: {data.get('queryCode')}")
        return data

    def collect(self, delay=0.15, max_pages=None):
        self.initialize()
        rows = []
        seen = set()
        page = 1
        expected = None
        while True:
            data = self.get_page(page)
            batch = data.get("fetchResultListSet") or []
            if not batch:
                break
            if expected is None:
                expected = int(batch[0].get("TOTAL_COUNT") or 0)
            for row in batch:
                code = str(row.get("STORE_CD") or "")
                if code and code not in seen:
                    seen.add(code)
                    rows.append(row)
            next_page = int(float(batch[0].get("NEXT_PAGE") or 0))
            print(f"투썸 매장 수집: {len(rows)}/{expected or '?'} (페이지 {page})", flush=True)
            if not next_page or (max_pages and page >= max_pages):
                break
            page = next_page
            if delay:
                sleep(delay)
        if not max_pages and expected and len(rows) != expected:
            raise RuntimeError(f"전체 매장 수 불일치: API {expected}개, 수집 {len(rows)}개")
        return rows

    def get_store_images(self, row):
        store_code = row.get("STORE_CD") or row.get("id") or ""
        latitude = row.get("LAT") or row.get("lat") or ""
        longitude = row.get("LONGI") or row.get("lng") or ""
        form = urlencode({
            "storeCd": store_code,
            "lat": latitude,
            "longi": longitude,
        }).encode()
        headers = dict(self.headers)
        headers["Referer"] = (
            f"{BASE_URL}/ts/tsSearchStoreDetail.do?storeCd={store_code}"
        )
        request = Request(BASE_URL + DETAIL_API, data=form, headers=headers, method="POST")
        with urlopen(request, timeout=self.timeout) as response:
            body = response.read()
        if not body.strip():
            raise RuntimeError("상세 API 빈 응답")
        data = json.loads(body)
        if str(data.get("queryCode")) != "1000":
            raise RuntimeError(data.get("queryMessage") or "상세 API 오류")
        details = data.get("fetchResultListSet") or []
        return image_list(details[0].get("STORE_IMG")) if details else []

    def collect_images(self, rows, workers=2, request_delay=0.5):
        """상세 API의 전체 이미지 목록을 병렬 수집한다."""
        completed = 0
        failures = 0

        def fetch(row):
            for attempt in range(4):
                try:
                    if request_delay:
                        sleep(request_delay)
                    return self.get_store_images(row)
                except Exception:
                    if attempt == 3:
                        raise
                    sleep(3 * (2 ** attempt))

        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {executor.submit(fetch, row): row for row in rows}
            for future in as_completed(futures):
                row = futures[future]
                try:
                    images = future.result()
                    if images:
                        row["_IMAGES"] = images
                except Exception as error:
                    failures += 1
                    code = row.get("STORE_CD") or row.get("id")
                    print(f"이미지 수집 실패: {code} - {error}", flush=True)
                completed += 1
                if completed % 50 == 0 or completed == len(rows):
                    print(f"투썸 이미지 수집: {completed}/{len(rows)}", flush=True)
        if failures:
            print(f"상세 이미지 수집 실패 {failures}개: 대표 이미지로 대체", flush=True)
        return rows


def payload(rows, current_date):
    items = sorted((build_store(row) for row in rows), key=lambda item: (item["name"], item["id"]))
    return {
        "kind": "Korea Twosome Place Stores",
        "date": current_date,
        "source": "투썸플레이스 공식 매장 찾기",
        "source_url": SOURCE_URL,
        "count": len(items),
        "item": items,
    }


def parse_args():
    parser = ArgumentParser(description="투썸플레이스 전국 공식 매장 데이터 수집")
    parser.add_argument("--delay", type=float, default=0.15, help="페이지 요청 사이 대기 시간(초)")
    parser.add_argument("--timeout", type=float, default=30, help="HTTP 요청 제한 시간(초)")
    parser.add_argument("--max-pages", type=int, help="개발 확인용 최대 페이지 수")
    parser.add_argument("--image-workers", type=int, default=2, help="상세 이미지 동시 요청 수")
    parser.add_argument("--image-delay", type=float, default=0.5, help="상세 이미지 요청 전 대기 시간(초)")
    parser.add_argument("--skip-images", action="store_true", help="상세 이미지 조회를 생략합니다.")
    parser.add_argument(
        "--refresh-images-existing", action="store_true",
        help="목록 재수집 없이 기존 stores.json의 상세 이미지만 갱신합니다.",
    )
    parser.add_argument(
        "--reformat-existing", action="store_true",
        help="네트워크 수집 없이 기존 JSON 3개를 간결한 형식으로 정리합니다.",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    current_date = datetime.now(ZoneInfo("Asia/Seoul")).strftime("%Y-%m-%d")
    if args.reformat_existing:
        paths = [
            OUTPUT_DIR / "stores.json",
            OUTPUT_DIR / "latest.json",
            OUTPUT_DIR / current_date[:4] / f"twosome_{current_date}.json",
        ]
        for path in paths:
            with path.open(encoding="utf-8") as file:
                data = json.load(file)
            write_json(path, compact_existing(data))
            print(f"형식 정리 완료: {path}", flush=True)
        return
    if args.refresh_images_existing:
        with (OUTPUT_DIR / "stores.json").open(encoding="utf-8") as file:
            data = json.load(file)
        items = data.get("item", [])
        Collector(timeout=args.timeout).collect_images(
            items, workers=args.image_workers, request_delay=args.image_delay,
        )
        for item in items:
            images = item.pop("_IMAGES", None)
            old_image = item.pop("image", None)
            item["images"] = images or item.get("images") or ([old_image] if old_image else [])
        paths = [
            OUTPUT_DIR / "stores.json",
            OUTPUT_DIR / "latest.json",
            OUTPUT_DIR / current_date[:4] / f"twosome_{current_date}.json",
        ]
        for path in paths:
            write_json(path, data)
        print(f"기존 투썸 이미지 갱신 완료: {len(items)}개", flush=True)
        return
    rows = Collector(timeout=args.timeout).collect(delay=args.delay, max_pages=args.max_pages)
    if not args.skip_images:
        sleep(5)
        Collector(timeout=args.timeout).collect_images(
            rows, workers=args.image_workers, request_delay=args.image_delay,
        )
    data = payload(rows, current_date)

    if args.max_pages:
        output = OUTPUT_DIR / "preview.json"
        write_json(output, data)
        print(f"미리보기 저장 완료: {output} ({data['count']}개)", flush=True)
        return

    snapshot = OUTPUT_DIR / current_date[:4] / f"twosome_{current_date}.json"
    write_json(snapshot, data)
    write_json(OUTPUT_DIR / "latest.json", data)
    write_json(OUTPUT_DIR / "stores.json", data)
    print(f"투썸 매장 데이터 저장 완료: {data['count']}개", flush=True)


if __name__ == "__main__":
    main()
