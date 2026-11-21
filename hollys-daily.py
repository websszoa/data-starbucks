"""할리스 매장 수집기 (GitHub Actions 에서 매주 월요일 실행).

1. 매장 목록 페이지로 매장 수(count)를 확인해 기록한다.
2. 매장 수나 매장 구성이 바뀌었으면 상세 정보를 수집해 위치·상세 파일을 저장한다.
3. 직접 관리하는 hollys/stores.json 과 비교해 신규·폐점 매장을 기록한다.

출력
- hollys/count/{year}/count_{date}.json, hollys/count/latest.json         : 매일
- hollys/location/{year}/location_{date}.json, hollys/location/latest.json : 변화가 있을 때
- hollys/details/{year}/details_{date}.json, hollys/details/latest.json    : 변화가 있을 때
- hollys/changes/latest.json                                               : 매일 (stores.json 이 있을 때)

robots.txt 에서 /store 경로는 허용되어 있다. 요청 사이에 일정 간격을 두고,
자동 수집기임을 숨기지 않는다.

실행: python hollys-daily.py
"""

from datetime import datetime
from html import unescape
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo
import json
import re
import time

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "hollys"
BASE_URL = "https://www.hollys.co.kr"
LIST_URL = f"{BASE_URL}/store/korea/korStore2.do"
DETAIL_URL = f"{BASE_URL}/store/korea/korStoreDetail2.do"
REQUEST_TIMEOUT = 20
REQUEST_INTERVAL = 1.5
MAX_RETRIES = 3
MAX_PAGES = 300


# ── 요청 ──────────────────────────────────────────────────────────
def fetch(url, params):
    request = Request(
        f"{url}?{urlencode(params)}",
        headers={"User-Agent": "HollysStoreCollector/1.0", "Referer": LIST_URL},
    )
    for attempt in range(1, MAX_RETRIES + 1):
        time.sleep(REQUEST_INTERVAL)
        try:
            with urlopen(request, timeout=REQUEST_TIMEOUT) as response:
                return response.read().decode("utf-8", errors="replace")
        except HTTPError as error:
            if attempt == MAX_RETRIES:
                raise RuntimeError(f"요청 실패: {url} {params} ({error})") from error
            wait = 60 if error.code == 429 else attempt * 2
            time.sleep(wait)
        except (URLError, TimeoutError) as error:
            if attempt == MAX_RETRIES:
                raise RuntimeError(f"요청 실패: {url} {params} ({error})") from error
            time.sleep(attempt * 2)


def text(html):
    value = re.sub(r"<br\s*/?>", "\n", html or "", flags=re.I)
    value = re.sub(r"<!--.*?-->", "", value, flags=re.S)
    value = unescape(re.sub(r"<[^>]+>", "", value))
    lines = [re.sub(r"[ \t\r]+", " ", line).strip() for line in value.split("\n")]
    return "\n".join(line for line in lines if line).strip()


def clean_tail(value):
    """원문 끝에 붙은 ' .' 같은 찌꺼기를 지운다."""
    return re.sub(r"\s*\.\s*$", "", value or "").strip()


# ── 목록 ──────────────────────────────────────────────────────────
def parse_list_page(html):
    table = re.search(r"<table.*?</table>", html, re.S)
    if not table:
        return []
    stores = []
    for row in re.findall(r"<tr>(.*?)</tr>", table.group(0), re.S):
        row = re.sub(r"<!--.*?-->", "", row, flags=re.S)
        cells = re.findall(r"<td[^>]*>(.*?)</td>", row, re.S)
        branch = re.search(r"storeView\((\d+)\)", row)
        if len(cells) < 6 or not branch:
            continue
        stores.append({
            "id": branch.group(1),
            "region": text(cells[0]),
            "name": text(cells[1]),
            "status": text(cells[2]),
            "address": clean_tail(text(cells[3])),
            "services": re.findall(r"""alt=["']([^"']+)["']""", cells[4]),
            "phone": clean_tail(text(cells[5])),
        })
    return stores


def get_store_list():
    stores = {}
    previous_ids = None
    for page in range(1, MAX_PAGES + 1):
        rows = parse_list_page(fetch(LIST_URL, {"pageNo": page, "sido": "", "gugun": "", "store": ""}))
        ids = [row["id"] for row in rows]
        # 마지막 페이지를 넘기면 빈 목록이나 같은 목록이 반복된다.
        if not rows or ids == previous_ids:
            break
        for row in rows:
            stores.setdefault(row["id"], row)
        previous_ids = ids
        print(f"목록 {page}페이지: 누적 {len(stores)}개", flush=True)
    else:
        raise RuntimeError(f"목록이 {MAX_PAGES}페이지를 넘었습니다. 페이지 종료 판정을 확인하세요.")
    if not stores:
        raise RuntimeError("매장 목록이 비어 있습니다. 페이지 구조가 바뀌었는지 확인하세요.")
    return list(stores.values())


# ── 상세 ──────────────────────────────────────────────────────────
def parse_detail(html):
    block = re.search(r'<div class="store_detail02">(.*?)<p><strong class="stit02">', html, re.S)
    body = block.group(1) if block else html
    fields = {}
    for label, value in re.findall(r'<strong class="stit">(.*?)</strong>\s*(<span.*?</span>)', body, re.S):
        fields[text(label)] = value
    coords = re.search(r"LatLng\(\s*'?([\d.]+)'?\s*,\s*'?([\d.]+)'?\s*\)", html)
    images = re.findall(r'src="(//admin\.hollys\.co\.kr/upload/branch/[^"]+)"', html)
    parking = clean_tail(text(fields.get("주차 정보", "")))
    return {
        "name": text(re.search(r'<p class="store_name">(.*?)</p>', html, re.S).group(1)) if "store_name" in html else "",
        "address": clean_tail(text(fields.get("주소", ""))),
        "directions": text(fields.get("찾아 오시는 길", "")),
        "phone": clean_tail(text(fields.get("전화번호", ""))),
        "hours": text(fields.get("영업시간", "")).split("\n") if text(fields.get("영업시간", "")) else [],
        "services": re.findall(r"""alt=["']([^"']+)["']""", fields.get("매장 서비스", "")),
        "parking": "" if parking == "-" else parking,
        "latitude": coords.group(1) if coords else "",
        "longitude": coords.group(2) if coords else "",
        "images": list(dict.fromkeys(f"https:{src}" for src in images)),
    }


def collect_details(stores):
    items, errors = [], []
    for index, store in enumerate(stores, start=1):
        try:
            detail = parse_detail(fetch(DETAIL_URL, {"branchId": store["id"]}))
            items.append({
                "id": store["id"],
                "name": detail["name"] or store["name"],
                "status": store["status"],
                "region": store["region"],
                "address": detail["address"] or store["address"],
                "directions": detail["directions"],
                "phone": detail["phone"] or store["phone"],
                "hours": detail["hours"],
                # 목록의 서비스 아이콘이 매장별 값이다. 상세 칸이 비어 있는 경우가 많다.
                "services": list(dict.fromkeys(store["services"] + detail["services"])),
                "parking": detail["parking"],
                "latitude": detail["latitude"],
                "longitude": detail["longitude"],
                "images": detail["images"],
            })
        except Exception as error:
            errors.append({"id": store["id"], "name": store["name"], "error": str(error)})
        if index % 25 == 0 or index == len(stores):
            print(f"상세 수집: {index}/{len(stores)}", flush=True)
    return items, errors


# ── 저장 ──────────────────────────────────────────────────────────
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


def save_dated(kind, today, data):
    write_json(DATA_DIR / kind / today[:4] / f"{kind}_{today}.json", data)
    write_json(DATA_DIR / kind / "latest.json", data)
    print(f"저장: hollys/{kind}/{today[:4]}/{kind}_{today}.json (+ latest.json)", flush=True)


def detect_change(previous_location, stores):
    if previous_location is None:
        return True, "이전 위치 데이터 없음"
    previous_ids = {item["id"] for item in previous_location.get("item", [])}
    current_ids = {store["id"] for store in stores}
    if len(previous_ids) != len(current_ids):
        return True, f"매장 수 변경 {len(previous_ids)} → {len(current_ids)}"
    if previous_ids != current_ids:
        return True, f"매장 구성 변경 (추가 {len(current_ids - previous_ids)}, 삭제 {len(previous_ids - current_ids)})"
    return False, "변경 없음"


def write_changes(today, stores, details):
    """직접 관리하는 hollys/stores.json 과 실제 목록을 비교한다."""
    managed = load_json(DATA_DIR / "stores.json")
    if managed is None:
        print("hollys/stores.json 이 없어 변경 비교를 건너뜁니다. (python hollys-stores.py 로 생성)", flush=True)
        return
    spec = spec_from_file_location("hollys_stores", ROOT / "hollys-stores.py")
    builder = module_from_spec(spec)
    spec.loader.exec_module(builder)

    managed_ids = {item["id"]: item for item in managed.get("item", [])}
    current = {store["id"]: store for store in stores}
    detail_by_id = {item["id"]: item for item in (details or {}).get("item", [])}
    previous = load_json(DATA_DIR / "changes" / "latest.json", {}) or {}
    since_by_id = {row["id"]: row.get("since") for row in previous.get("added", []) + previous.get("removed", [])}

    added = []
    for store_id, store in current.items():
        if store_id in managed_ids:
            continue
        detail = detail_by_id.get(store_id)
        added.append({
            "id": store_id, "name": store["name"], "address": store["address"], "status": store["status"],
            "since": since_by_id.get(store_id) or today,
            "draft": builder.build_store(detail) if detail else None,
        })
    removed = [
        {"id": store_id, "name": item.get("name"), "address": item.get("address"),
         "since": since_by_id.get(store_id) or today}
        for store_id, item in managed_ids.items() if store_id not in current
    ]
    write_json(DATA_DIR / "changes" / "latest.json", {
        "kind": "Korea Hollys Store Changes", "date": today,
        "stores_count": len(managed_ids), "current_count": len(current),
        "added_count": len(added), "removed_count": len(removed),
        "added": added, "removed": removed,
    })
    print(f"변경 비교: 신규 {len(added)}개 / 폐점 {len(removed)}개", flush=True)


def main():
    today = datetime.now(ZoneInfo("Asia/Seoul")).strftime("%Y-%m-%d")
    print(f"할리스 수집 기준일: {today}", flush=True)

    stores = get_store_list()
    statuses = {}
    for store in stores:
        statuses[store["status"]] = statuses.get(store["status"], 0) + 1
    save_dated("count", today, {"kind": "Korea Hollys", "date": today, "count": len(stores), "status": statuses})

    previous_location = load_json(DATA_DIR / "location" / "latest.json")
    changed, reason = detect_change(previous_location, stores)
    print(f"판정: {reason}", flush=True)

    errors = []
    if changed:
        items, errors = collect_details(stores)
        details = {"kind": "Korea Hollys", "date": today, "source_count": len(stores),
                   "count": len(items), "failed_count": len(errors), "errors": errors, "item": items}
        write_json(DATA_DIR / "details" / today[:4] / f"details_{today}.json", details)
        if not errors:  # 일부 실패한 불완전한 데이터는 latest 에 반영하지 않는다.
            write_json(DATA_DIR / "details" / "latest.json", details)
        print(f"저장: hollys/details/{today[:4]}/details_{today}.json" + ("" if errors else " (+ latest.json)"), flush=True)

        location = [{
            "id": item["id"], "name": item["name"], "status": item["status"], "address": item["address"],
            "latitude": item["latitude"], "longitude": item["longitude"],
        } for item in items]
        # 상세 수집에 실패한 매장도 목록 기준으로는 남긴다 (좌표 없음).
        failed = {error["id"] for error in errors}
        location += [{"id": s["id"], "name": s["name"], "status": s["status"], "address": s["address"],
                      "latitude": "", "longitude": ""} for s in stores if s["id"] in failed]
        save_dated("location", today, {"kind": "Korea Hollys", "date": today, "count": len(location), "item": location})
    else:
        print("매장 구성 변경이 없어 상세 수집을 생략합니다.", flush=True)

    write_changes(today, stores, load_json(DATA_DIR / "details" / "latest.json"))

    if errors:
        raise RuntimeError(f"{len(errors)}개 매장 상세 수집 실패. details 파일의 errors 를 확인하세요.")


if __name__ == "__main__":
    main()
