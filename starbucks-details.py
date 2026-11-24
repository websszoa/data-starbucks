from datetime import datetime
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
import json
import argparse
import time

# 스타벅스 데이터는 모두 starbucks/ 폴더에 저장한다.
DATA_DIR = Path(__file__).resolve().parent / "starbucks"


BASE_URL = "https://www.starbucks.co.kr"
REFERER = f"{BASE_URL}/store/store_map.do?disp=locale"
REQUEST_TIMEOUT = 20
MAX_RETRIES = 3
REQUEST_INTERVAL = 1.5
DEFAULT_RATE_LIMIT_WAIT = 60
FAILED_RETRY_WAIT = 30

REGIONS = {
    "seoul": {"name": "서울", "code": "01"},
    "gyeonggi": {"name": "경기", "code": "08"},
    "daegu": {"name": "대구", "code": "03"},
    "daejeon": {"name": "대전", "code": "04"},
    "busan": {"name": "부산", "code": "05"},
    "ulsan": {"name": "울산", "code": "06"},
    "incheon": {"name": "인천", "code": "07"},
    "gangwon": {"name": "강원", "code": "09"},
    "gyeongnam": {"name": "경남", "code": "10"},
    "gyeongbuk": {"name": "경북", "code": "11"},
    "jeonnam-gwangju": {"name": "전남광주", "code": "12"},
    "jeonbuk": {"name": "전북", "code": "13"},
    "chungnam": {"name": "충남", "code": "14"},
    "chungbuk": {"name": "충북", "code": "15"},
    "jeju": {"name": "제주", "code": "16"},
    "sejong": {"name": "세종", "code": "17"},
}

SERVICE_THEME_IMAGES = {
    "T09": "https://image.istarbucks.co.kr/upload/common/img/icon/icon04.png",
    "T10": "https://image.istarbucks.co.kr/upload/common/img/icon/icon05.png",
    "T22": "https://image.istarbucks.co.kr/upload/common/img/icon/icon18.png",
    "T21": "https://image.istarbucks.co.kr/upload/common/img/icon/icon20.png",
    "T05": "https://image.istarbucks.co.kr/upload/common/img/icon/icon22.png",
    "T30": "https://image.istarbucks.co.kr/upload/common/img/icon/icon23.png",
    "T43": "https://image.istarbucks.co.kr/upload/common/img/icon/icon_delivers_service.png",
    "T48": "https://image.istarbucks.co.kr/upload/common/img/icon/icon_eco_service.png",
    "Z9999": "https://image.istarbucks.co.kr/upload/common/img/icon/Moon_icon_16x16_230324.png",
    "T64": "https://image.istarbucks.co.kr/upload/common/img/icon/icon_fast_serve_02.png",
}

FACILITY_THEME_IMAGES = {
    "P02": "https://image.istarbucks.co.kr/upload/common/img/icon/icon_pet_01.png",
    "P10": "https://image.istarbucks.co.kr/upload/common/img/icon/icon07.png",
    "P50": "https://image.istarbucks.co.kr/upload/common/img/icon/icon09.png",
    "P20": "https://image.istarbucks.co.kr/upload/common/img/icon/icon08.png",
    "P60": "https://image.istarbucks.co.kr/upload/common/img/icon/icon12.png",
    "P70": "https://image.istarbucks.co.kr/upload/common/img/icon/icon13.png",
    "P40": "https://image.istarbucks.co.kr/upload/common/img/icon/icon10.png",
    "P80": "https://image.istarbucks.co.kr/upload/common/img/icon/icon14.png",
    "P90": "https://image.istarbucks.co.kr/upload/common/img/icon/icon21.png",
    "P01": "https://image.istarbucks.co.kr/upload/common/img/icon/EV_icon_map.png",
    "WHCROAD": "https://image.istarbucks.co.kr/upload/common/img/icon/icon19.png",
}

WEEKDAYS = {
    "1": "일요일", "2": "월요일", "3": "화요일", "4": "수요일",
    "5": "목요일", "6": "금요일", "7": "토요일",
}


def post_json(path, data):
    """스타벅스 공개 웹 API에 POST 요청하고 일시적 오류는 재시도한다."""
    body = urlencode(data).encode("utf-8")
    request = Request(
        f"{BASE_URL}{path}",
        data=body,
        headers={
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            "Referer": REFERER,
            "User-Agent": "StarbucksStoreCollector/1.0",
            "X-Requested-With": "XMLHttpRequest",
        },
        method="POST",
    )
    for attempt in range(1, MAX_RETRIES + 1):
        # 자동화 요청임을 숨기지 않고 일정한 저속 간격을 유지한다.
        time.sleep(REQUEST_INTERVAL)
        try:
            with urlopen(request, timeout=REQUEST_TIMEOUT) as response:
                return json.loads(response.read().decode("utf-8"))
        except HTTPError as error:
            if attempt == MAX_RETRIES:
                raise RuntimeError(f"API 요청 실패: {path} ({error})") from error
            if error.code == 429:
                retry_after = error.headers.get("Retry-After")
                wait_seconds = int(retry_after) if retry_after and retry_after.isdigit() else DEFAULT_RATE_LIMIT_WAIT
                print(f"요청 제한 감지: {wait_seconds}초 후 재시도합니다.", flush=True)
                time.sleep(wait_seconds)
            else:
                time.sleep(attempt * 2)
        except (URLError, TimeoutError, json.JSONDecodeError) as error:
            if attempt == MAX_RETRIES:
                raise RuntimeError(f"API 요청 실패: {path} ({error})") from error
            time.sleep(attempt * 2)


def get_region_stores(region):
    data = post_json("/store/getStore.do", {
        "ins_lat": "35.1796", "ins_lng": "129.0756", "search_text": "",
        "p_sido_cd": region["code"], "p_gugun_cd": "", "in_distance": "0",
        "in_biz_cd": "", "in_biz_cds": "0", "in_scodes": "0",
        "isError": "true", "iend": "1000", "searchType": "C", "set_date": "",
    })
    stores = data.get("list", [])
    if not stores:
        raise RuntimeError(f'{region["name"]} 매장 목록이 비어 있습니다.')
    return stores


def absolute_image_url(path):
    if not path:
        return ""
    if path.startswith(("http://", "https://")):
        return path
    return f"{BASE_URL}/{path.lstrip('/')}"


def format_hours(rows):
    result = []
    for row in rows:
        day = row.get("store_time_day", "")
        weekday = WEEKDAYS.get(str(row.get("store_time_week", "")), "")
        hours = row.get("store_opentime") or "휴점"
        if hours != "휴점" and len(hours) == 9 and "-" in hours:
            opening, closing = hours.split("-", 1)
            hours = f"{opening[:2]}:{opening[2:]}-{closing[:2]}:{closing[2:]}"
        result.append(f"{day}일 {weekday} {hours}".strip())
    return result


def collect_store(index, summary):
    biz_code = summary.get("s_biz_code", "")
    detail_rows = post_json("/store/getStoreView.do", {"in_biz_cd": biz_code}).get("view", [])
    if not detail_rows:
        raise RuntimeError(f"상세 정보가 없습니다: {biz_code}")
    detail = detail_rows[0]
    hours = post_json("/store/getStoreTime.do", {
        "in_biz_cd": biz_code, "in_store_type": "C",
    }).get("list", [])

    theme_codes = set(filter(None, (detail.get("theme_state") or "").split("@")))
    images = [absolute_image_url(detail.get("defaultimage"))]
    images.extend(absolute_image_url(path) for path in
                  (detail.get("etcimage") or "").split(",") if path)
    return {
        "number": index + 1,
        "code": biz_code,
        "name": detail.get("s_name", summary.get("s_name", "")),
        "description": detail.get("notice") or "",
        "address": detail.get("doro_address") or detail.get("addr") or "",
        "parking": detail.get("park_info") or "",
        "directions": detail.get("map_desc") or "",
        "phone": detail.get("tel") or "",
        "latitude": detail.get("lat") or summary.get("lat") or "",
        "longitude": detail.get("lot") or summary.get("lot") or "",
        "services": [url for code, url in SERVICE_THEME_IMAGES.items() if code in theme_codes],
        "facilities": [url for code, url in FACILITY_THEME_IMAGES.items()
                       if code in theme_codes or
                       (code == "WHCROAD" and detail.get("whcroad_yn") == "WHCROAD")],
        "images": [url for url in images if url],
        "hours": format_hours(hours),
        # 아이콘 매핑에 없는 테마도 잃지 않도록 원본 코드를 함께 저장한다.
        "theme_codes": sorted(theme_codes | ({"WHCROAD"} if detail.get("whcroad_yn") == "WHCROAD" else set())),
    }


def validate(stores, collected, region):
    source_codes = [store.get("s_biz_code") for store in stores]
    result_codes = [store.get("code") for store in collected]
    if len(source_codes) != len(set(source_codes)):
        raise RuntimeError("API 매장 목록에 중복 매장 코드가 있습니다.")
    if len(result_codes) != len(set(result_codes)):
        raise RuntimeError("수집 결과에 중복 매장 코드가 있습니다.")
    address_prefixes = {
        "경남": ("경상남도", "경남"),
        "경북": ("경상북도", "경북"),
        "전남광주": ("전라남도", "전남", "광주광역시", "광주"),
        "충남": ("충청남도", "충남"),
        "충북": ("충청북도", "충북"),
    }
    prefixes = address_prefixes.get(region["name"], (region["name"],))
    invalid = [store["name"] for store in collected
               if not store["address"].startswith(prefixes)]
    if invalid:
        raise RuntimeError(
            f'{region["name"]} 권역이 아닌 주소가 포함되어 있습니다: {", ".join(invalid[:5])}'
        )


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as file:
        json.dump(data, file, ensure_ascii=False, indent=4)


def format_open_date(value):
    """'20190530' → '2019-05-30'. 형식이 다르면 빈 문자열."""
    value = str(value or "").strip()
    return f"{value[:4]}-{value[4:6]}-{value[6:8]}" if len(value) == 8 and value.isdigit() else ""


def save_location_data(slug, region, stores, current_date):
    """매장 목록 API의 기본 정보로 지역별 좌표 파일을 저장한다."""
    items = []
    for store in stores:
        latitude = store.get("lat") or ""
        longitude = store.get("lot") or ""
        if not latitude or not longitude:
            raise RuntimeError(f'좌표가 없는 매장이 있습니다: {store.get("s_name", "") }')
        items.append({
            "code": store.get("s_biz_code") or "",
            "name": store.get("s_name") or "",
            "address": store.get("doro_address") or store.get("addr") or "",
            "latitude": latitude,
            "longitude": longitude,
            "open_dt": format_open_date(store.get("open_dt")),  # 개점일
            # 타입(DT·리저브 등)·위생등급·리조트처럼 상세 아이콘에 없는 코드까지 보관한다.
            "theme_codes": sorted(filter(None, (store.get("theme_state") or "").split("@"))),
        })

    codes = [item["code"] for item in items]
    if len(codes) != len(set(codes)):
        raise RuntimeError(f'{region["name"]} 위치 데이터에 중복 매장 코드가 있습니다.')

    year = current_date[:4]
    output_dir = DATA_DIR / "location" / slug / year
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"{slug}_{current_date}.json"
    data = {
        "location": f'{region["name"]}({slug})',
        "region_code": region["code"],
        "count": len(items),
        "date": current_date,
        "item": items,
    }
    write_json(output_path, data)
    write_json(DATA_DIR / "location" / slug / "latest.json", data)
    print(f"위치 파일이 저장되었습니다: {output_path} (+ latest.json)", flush=True)
    return output_path


def collect_region(slug, current_date, stores=None, save_location=True):
    region = REGIONS[slug]
    year = current_date[:4]
    output_dir = DATA_DIR / "details" / slug / year
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"{slug}_{current_date}.json"

    stores = stores if stores is not None else get_region_stores(region)
    print(f'{region["name"]} 매장 {len(stores)}개를 확인했습니다.', flush=True)
    if save_location:
        save_location_data(slug, region, stores, current_date)
    results = {}
    failed = []
    for index, store in enumerate(stores):
        try:
            results[index] = collect_store(index, store)
        except Exception as error:
            failed.append((index, store, error))
            print(f'매장 수집 실패: {store.get("s_name", "")}({store.get("s_biz_code", "")}) - {error}', flush=True)
        print(f"수집 진행: {index + 1}/{len(stores)}", flush=True)

    # 일시적인 오류일 수 있으므로 실패한 매장만 잠시 쉬었다가 한 번 더 시도한다.
    errors = []
    if failed:
        print(f"\n실패한 매장 {len(failed)}개를 {FAILED_RETRY_WAIT}초 후 다시 시도합니다.", flush=True)
        time.sleep(FAILED_RETRY_WAIT)
        for index, store, _ in failed:
            name = f'{store.get("s_name", "")}({store.get("s_biz_code", "")})'
            try:
                results[index] = collect_store(index, store)
                print(f"재시도 성공: {name}", flush=True)
            except Exception as error:
                errors.append({"code": store.get("s_biz_code", ""),
                               "name": store.get("s_name", ""), "error": str(error)})
                print(f"재시도 실패: {name} - {error}", flush=True)
    collected = [results[index] for index in sorted(results)]
    validate(stores, collected, region)
    final_data = {
        "kind": "Korea Starbucks", "date": current_date,
        "location": f'{region["name"]}({slug})', "region_code": region["code"],
        "source_count": len(stores),
        "count": len(collected), "failed_count": len(errors),
        "errors": errors, "item": collected,
    }
    write_json(output_path, final_data)
    print(f"파일이 저장되었습니다: {output_path}", flush=True)
    # 일부 매장 수집에 실패한 불완전한 데이터는 latest.json에 반영하지 않는다.
    if not errors:
        write_json(DATA_DIR / "details" / slug / "latest.json", final_data)
        print(f"latest.json 갱신: details/{slug}/latest.json", flush=True)
    if errors:
        raise RuntimeError(f"{len(errors)}개 매장의 상세 정보 수집에 실패했습니다. errors 항목을 확인하세요.")


def parse_args():
    parser = argparse.ArgumentParser(description="스타벅스 지역별 매장 상세 정보 수집")
    parser.add_argument(
        "regions", nargs="*", metavar="REGION",
        help="수집할 지역 slug. 생략하면 전체 지역을 수집합니다.",
    )
    args = parser.parse_args()
    invalid = [region for region in args.regions if region not in REGIONS]
    if invalid:
        parser.error(
            f"지원하지 않는 지역: {', '.join(invalid)}\n"
            f"사용 가능한 지역: {', '.join(sorted(REGIONS))}"
        )
    return args


def main():
    args = parse_args()
    selected = args.regions or list(REGIONS)
    current_date = datetime.now().strftime("%Y-%m-%d")
    failures = []
    for position, slug in enumerate(selected, start=1):
        print(f"\n지역 수집 시작: {REGIONS[slug]['name']} ({position}/{len(selected)})", flush=True)
        try:
            collect_region(slug, current_date)
        except Exception as error:
            failures.append((slug, str(error)))
            print(f"지역 수집 실패: {REGIONS[slug]['name']} - {error}", flush=True)
    if failures:
        summary = "; ".join(f"{slug}: {error}" for slug, error in failures)
        raise RuntimeError(f"{len(failures)}개 지역 수집 실패: {summary}")


if __name__ == "__main__":
    main()
