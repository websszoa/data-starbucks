"""매일 위치 스냅샷을 만들고 변경된 지역만 상세 정보를 수집한다."""

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


def load_previous_snapshot(slug, current_date):
    """오늘 파일을 제외한 가장 최근의 정상 위치 스냅샷을 읽는다."""
    region_dir = Path("location") / slug
    candidates = []
    if not region_dir.exists():
        return None

    for path in region_dir.glob("*/*.json"):
        try:
            with path.open(encoding="utf-8") as file:
                data = json.load(file)
            snapshot_date = data.get("date", "")
            if snapshot_date and snapshot_date < current_date:
                candidates.append((snapshot_date, path, data))
        except (OSError, json.JSONDecodeError):
            print(f"손상된 위치 파일을 건너뜁니다: {path}", flush=True)

    if not candidates:
        return None
    return max(candidates, key=lambda candidate: candidate[0])[2]


def detect_change(previous, stores):
    current_codes = {store.get("s_biz_code") for store in stores if store.get("s_biz_code")}
    if previous is None:
        return True, "이전 위치 데이터 없음"

    previous_items = previous.get("item", [])
    previous_codes = {item.get("code") for item in previous_items if item.get("code")}
    previous_count = previous.get("count", len(previous_items))
    current_count = len(stores)

    if previous_count != current_count:
        return True, f"매장 수 변경 {previous_count} → {current_count}"
    if previous_codes != current_codes:
        added = len(current_codes - previous_codes)
        removed = len(previous_codes - current_codes)
        return True, f"매장 코드 변경 (추가 {added}, 삭제 {removed})"
    return False, "변경 없음"


def main():
    collector = load_collector()
    current_date = datetime.now(ZoneInfo("Asia/Seoul")).strftime("%Y-%m-%d")
    changed_regions = []
    stores_by_region = {}
    failures = []

    print(f"일일 수집 기준일: {current_date}", flush=True)
    for position, (slug, region) in enumerate(collector.REGIONS.items(), start=1):
        print(f"\n위치 확인: {region['name']} ({position}/{len(collector.REGIONS)})", flush=True)
        try:
            previous = load_previous_snapshot(slug, current_date)
            stores = collector.get_region_stores(region)
            changed, reason = detect_change(previous, stores)
            collector.save_location_data(slug, region, stores, current_date)
            print(f"변경 판정: {reason}", flush=True)
            if changed:
                changed_regions.append(slug)
                stores_by_region[slug] = stores
        except Exception as error:
            failures.append((slug, f"위치 수집: {error}"))

    if changed_regions:
        names = ", ".join(collector.REGIONS[slug]["name"] for slug in changed_regions)
        print(f"\n상세 수집 대상: {names}", flush=True)
    else:
        print("\n매장 구성 변경이 없어 상세 수집을 생략합니다.", flush=True)

    for slug in changed_regions:
        try:
            collector.collect_region(
                slug,
                current_date,
                stores=stores_by_region[slug],
                save_location=False,
            )
        except Exception as error:
            failures.append((slug, f"상세 수집: {error}"))

    if failures:
        summary = "; ".join(f"{slug}: {error}" for slug, error in failures)
        raise RuntimeError(f"{len(failures)}개 작업 실패: {summary}")


if __name__ == "__main__":
    main()
