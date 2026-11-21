"""직접 관리하는 starbucks/stores.json 과 실제 매장 목록(location)을 비교해 신규·폐점 매장을 기록한다.

실행: python starbucks-changes.py  (매일 수집 후 자동 실행)

출력: starbucks/changes/latest.json
- added   : 실제 목록에는 있는데 stores.json 에 없는 매장 (신규)
            draft 에 stores.json 과 같은 형식의 초안을 넣어 두므로 검토 후 복사해 쓰면 된다.
- removed : stores.json 에는 있는데 실제 목록에서 사라진 매장 (폐점)
- since   : 그 변화가 처음 확인된 날짜 (위치 스냅샷 기록 기준)

stores.json 에 반영하면 다음 실행부터 목록에서 빠진다.
"""

from datetime import datetime
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from zoneinfo import ZoneInfo
import json

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "starbucks"


def load_stores_module():
    spec = spec_from_file_location("starbucks_stores", ROOT / "starbucks-stores.py")
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_json(path, default=None):
    try:
        with path.open(encoding="utf-8") as file:
            return json.load(file)
    except (OSError, json.JSONDecodeError):
        return default


def region_history(slug):
    """지역의 위치 스냅샷을 날짜순으로 [(date, {code: item})] 반환한다."""
    snapshots = []
    for path in (DATA_DIR / "location" / slug).glob("*/*.json"):
        data = load_json(path)
        if data and data.get("date"):
            snapshots.append((data["date"], {item["code"]: item for item in data.get("item", [])}))
    return sorted(snapshots, key=lambda snapshot: snapshot[0])


def first_seen(history, code):
    """마지막으로 연속해서 목록에 있기 시작한 날짜"""
    date = None
    for snapshot_date, items in history:
        if code in items:
            date = date or snapshot_date
        else:
            date = None
    return date


def missing_since(history, code):
    """마지막으로 목록에 있었던 다음 스냅샷 날짜"""
    seen = False
    date = None
    for snapshot_date, items in history:
        if code in items:
            seen, date = True, None
        elif seen and date is None:
            date = snapshot_date
    return date


def main():
    stores_module = load_stores_module()
    today = datetime.now(ZoneInfo("Asia/Seoul")).strftime("%Y-%m-%d")

    managed = load_json(DATA_DIR / "stores.json")
    if managed is None:
        raise SystemExit("starbucks/stores.json 을 읽을 수 없습니다.")
    managed_items = {item["id"]: item for item in managed.get("item", [])}

    current = {}      # code → (slug, location item)
    histories = {}
    missing_regions = []
    for slug in stores_module.REGIONS:
        location = load_json(DATA_DIR / "location" / slug / "latest.json")
        if location is None:
            missing_regions.append(slug)
            continue
        histories[slug] = region_history(slug)
        for item in location.get("item", []):
            current[item["code"]] = (slug, item)

    if missing_regions:
        # 일부 지역을 못 읽으면 그 지역 매장이 전부 폐점으로 보이므로 비교하지 않는다.
        raise SystemExit(f"위치 데이터를 읽지 못한 지역이 있어 비교를 건너뜁니다: {', '.join(missing_regions)}")

    added = []
    for code, (slug, location_item) in current.items():
        if code in managed_items:
            continue
        details = load_json(DATA_DIR / "details" / slug / "latest.json", {}) or {}
        detail = next((item for item in details.get("item", []) if item.get("code") == code), None)
        draft, _ = stores_module.build_store(slug, location_item, detail)
        draft = stores_module.normalize_starbucks(draft)
        draft.pop("region", None)
        added.append({
            "id": code,
            "name": location_item.get("name"),
            "address": location_item.get("address", "").strip(),
            "since": first_seen(histories[slug], code) or today,
            "draft": draft,
        })

    removed = []
    for store_id, item in managed_items.items():
        if store_id in current:
            continue
        since = None
        for history in histories.values():
            since = missing_since(history, store_id)
            if since:
                break
        removed.append({
            "id": store_id,
            "name": item.get("name"),
            "address": item.get("address"),
            "since": since or today,
        })

    added.sort(key=lambda row: (row["since"], row["name"] or ""), reverse=True)
    removed.sort(key=lambda row: (row["since"], row["name"] or ""), reverse=True)

    data = {
        "kind": "Korea Starbucks Store Changes",
        "date": today,
        "stores_count": len(managed_items),   # stores.json 매장 수
        "current_count": len(current),        # 실제 영업 매장 수
        "added_count": len(added),
        "removed_count": len(removed),
        "added": added,
        "removed": removed,
    }
    output = DATA_DIR / "changes" / "latest.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8") as file:
        json.dump(data, file, ensure_ascii=False, indent=4)
        file.write("\n")
    print(f"신규 {len(added)}개 / 폐점 {len(removed)}개 → {output.relative_to(ROOT)}", flush=True)


if __name__ == "__main__":
    main()
