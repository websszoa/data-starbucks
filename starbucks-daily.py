"""매일 지역별 매장 수(count)를 기록하고, 변화가 있는 지역만 위치/상세 정보를 수집한다.

- count/{year}/count_{date}.json : 매일 저장 (전 지역 매장 수)
- location/{slug}/{year}/...      : 변화가 있는 지역만 저장
- details/{slug}/{year}/...       : 변화가 있는 지역만 저장
"""

from datetime import datetime
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from zoneinfo import ZoneInfo
import json


def load_collector():
    module_path = Path(__file__).with_name("starbucks-details.py")
    spec = spec_from_file_location("starbucks_details", module_path)
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def latest_json(paths, current_date):
    """date 필드가 오늘보다 이전인 파일 중 가장 최근 데이터를 반환한다."""
    candidates = []
    for path in paths:
        try:
            with path.open(encoding="utf-8") as file:
                data = json.load(file)
        except (OSError, json.JSONDecodeError):
            print(f"손상된 파일을 건너뜁니다: {path}", flush=True)
            continue
        snapshot_date = data.get("date", "")
        if snapshot_date and snapshot_date < current_date:
            candidates.append((snapshot_date, data))
    if not candidates:
        return None
    return max(candidates, key=lambda candidate: candidate[0])[1]


def load_previous_counts(current_date):
    """가장 최근 count 파일의 지역별 매장 수를 읽는다."""
    data = latest_json(Path("count").glob("*/count_*.json"), current_date)
    if data is None:
        return {}
    return {
        slug: info["count"]
        for slug, info in data.get("regions", {}).items()
        if isinstance(info.get("count"), int)
    }


def load_previous_location(slug, current_date):
    """가장 최근에 저장된 위치 스냅샷(= 마지막으로 변화가 있었던 날)을 읽는다."""
    return latest_json((Path("location") / slug).glob("*/*.json"), current_date)


def detect_change(previous_count, previous_location, stores):
    current_count = len(stores)

    # 기준 카운트: count 파일 → 없으면 마지막 위치 스냅샷
    if previous_count is None and previous_location is not None:
        previous_count = previous_location.get("count", len(previous_location.get("item", [])))
    if previous_count is None:
        return True, "이전 데이터 없음"
    if previous_count != current_count:
        return True, f"매장 수 변경 {previous_count} → {current_count}"

    # 카운트가 같아도 폐점 1 + 신규 1처럼 구성이 바뀐 경우를 잡기 위한 보조 확인
    if previous_location is not None:
        previous_codes = {item.get("code") for item in previous_location.get("item", []) if item.get("code")}
        current_codes = {store.get("s_biz_code") for store in stores if store.get("s_biz_code")}
        if previous_codes != current_codes:
            added = len(current_codes - previous_codes)
            removed = len(previous_codes - current_codes)
            return True, f"매장 코드 변경 (추가 {added}, 삭제 {removed})"
    return False, "변경 없음"


def save_count_data(collector, current_date, counts, previous_counts, changed_regions):
    year = current_date[:4]
    output_dir = Path("count") / year
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"count_{current_date}.json"

    regions = {}
    for slug, region in collector.REGIONS.items():
        count = counts.get(slug)
        previous = previous_counts.get(slug)
        regions[slug] = {
            "name": region["name"],
            "region_code": region["code"],
            "count": count,  # 수집 실패 시 null
            "previous_count": previous,
            "diff": count - previous if count is not None and previous is not None else None,
            "changed": slug in changed_regions,
        }

    data = {
        "kind": "Korea Starbucks",
        "date": current_date,
        "total": sum(count for count in counts.values()),
        "changed_regions": changed_regions,
        "regions": regions,
    }
    with output_path.open("w", encoding="utf-8") as file:
        json.dump(data, file, ensure_ascii=False, indent=4)
    print(f"카운트 파일이 저장되었습니다: {output_path}", flush=True)


def main():
    collector = load_collector()
    current_date = datetime.now(ZoneInfo("Asia/Seoul")).strftime("%Y-%m-%d")
    previous_counts = load_previous_counts(current_date)
    counts = {}
    changed_regions = []
    stores_by_region = {}
    failures = []

    print(f"일일 수집 기준일: {current_date}", flush=True)

    # 1단계: 전 지역 카운트 확인
    for position, (slug, region) in enumerate(collector.REGIONS.items(), start=1):
        print(f"\n카운트 확인: {region['name']} ({position}/{len(collector.REGIONS)})", flush=True)
        try:
            stores = collector.get_region_stores(region)
            counts[slug] = len(stores)
            previous_location = load_previous_location(slug, current_date)
            changed, reason = detect_change(previous_counts.get(slug), previous_location, stores)
            print(f"매장 수 {len(stores)} / 판정: {reason}", flush=True)
            if changed:
                changed_regions.append(slug)
                stores_by_region[slug] = stores
        except Exception as error:
            failures.append((slug, f"카운트 수집: {error}"))
            print(f"카운트 수집 실패: {region['name']} - {error}", flush=True)

    save_count_data(collector, current_date, counts, previous_counts, changed_regions)

    if changed_regions:
        names = ", ".join(collector.REGIONS[slug]["name"] for slug in changed_regions)
        print(f"\n위치/상세 수집 대상: {names}", flush=True)
    else:
        print("\n매장 수 변화가 없어 위치/상세 수집을 생략합니다.", flush=True)

    # 2단계: 변화가 있는 지역만 위치 + 상세 저장
    for slug in changed_regions:
        region = collector.REGIONS[slug]
        stores = stores_by_region[slug]
        try:
            collector.save_location_data(slug, region, stores, current_date)
        except Exception as error:
            failures.append((slug, f"위치 저장: {error}"))
        try:
            collector.collect_region(slug, current_date, stores=stores, save_location=False)
        except Exception as error:
            failures.append((slug, f"상세 수집: {error}"))

    if failures:
        summary = "; ".join(f"{slug}: {error}" for slug, error in failures)
        raise RuntimeError(f"{len(failures)}개 작업 실패: {summary}")


if __name__ == "__main__":
    main()
