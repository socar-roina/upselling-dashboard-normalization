#!/usr/bin/env python3
"""PF 정규화(보험 넛지) 대시보드 데이터 생성기.
bq CLI로 BigQuery를 집계해 화면에 필요한 수치를 data.js(window.PF)로 출력.
사람이 손으로 숫자를 고치지 않아 정합성/누락/표현 변동이 구조적으로 없음.

정규화 시작 2026-05-21 14:00 KST. 노출(이벤트)은 어제까지, 수락(결제)은 D+1~2
정산이라 최근 2일은 일별 차트에서 비움(총계에는 포함).

usage: python3 gen_data.py [--cut YYYY-MM-DD] [--out data.js]
  --cut 집계 종료일(포함). 기본 = 어제 KST.
"""
import argparse, json, subprocess, sys
from datetime import datetime, timedelta, timezone

KST = timezone(timedelta(hours=9))
BQ_PROJECT = "socar-data"
START = "2026-05-21 14:00:00"
EVT = "socar-data.app_web_log.socar_app_web_log"
RSV = "socar-data.socar_biz_profit.profit_socar_reservation"
PF_FILTER = (r"(REGEXP_EXTRACT(form_data, r'upselling_type[\\:]+([A-Za-z]+)')='pfNudge' "
             r"OR REGEXP_EXTRACT(form_data, r'ut[\\:]+([a-z])')='p')")


def bq(sql):
    p = subprocess.run(["bq", "query", f"--project_id={BQ_PROJECT}", "--nouse_legacy_sql",
                        "--format=json", "--max_rows=100000", sql],
                       capture_output=True, text=True)
    if p.returncode != 0:
        sys.stderr.write(f"[bq error]\n{p.stderr}\n")
        raise SystemExit(2)
    return json.loads(p.stdout or "[]")


def num(x):
    return float(x) if x not in (None, "") else 0.0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cut", default=None)
    ap.add_argument("--out", default="data.js")
    a = ap.parse_args()
    cut = a.cut or (datetime.now(KST) - timedelta(days=1)).strftime("%Y-%m-%d")
    end = (datetime.strptime(cut, "%Y-%m-%d").date() + timedelta(days=1)).strftime("%Y-%m-%d") + " 00:00:00"

    # 1) 일별 노출(고유 고객)/수락(그날 PF0 전환)/전환 보험매출
    daily = bq(f"""
WITH v AS (
  SELECT DISTINCT DATE(event_at_kst) d, member_id
  FROM `{EVT}`
  WHERE page_name='upsell_bottomsheet' AND event_name='view' AND {PF_FILTER}
    AND event_at_kst >= DATETIME '{START}' AND event_at_kst < DATETIME '{end}'
    AND member_id IS NOT NULL
),
r AS (
  SELECT DATE(created_at_kst) d, member_id, _rev_pf
  FROM `{RSV}`
  WHERE pf_type='0' AND created_at_kst >= DATETIME '{START}' AND created_at_kst < DATETIME '{end}'
)
SELECT FORMAT_DATE('%Y-%m-%d', v.d) dt,
  COUNT(DISTINCT v.member_id) exposed,
  COUNT(DISTINCT IF(r.member_id IS NOT NULL, v.member_id, NULL)) accepted
FROM v LEFT JOIN r ON v.d=r.d AND v.member_id=r.member_id
GROUP BY 1 ORDER BY 1""")

    # 2) 노출 고객 수(전 기간 고유), 전환 보험매출 합계/평균
    tot = bq(f"""
WITH v AS (
  SELECT DISTINCT DATE(event_at_kst) d, member_id
  FROM `{EVT}`
  WHERE page_name='upsell_bottomsheet' AND event_name='view' AND {PF_FILTER}
    AND event_at_kst >= DATETIME '{START}' AND event_at_kst < DATETIME '{end}'
    AND member_id IS NOT NULL
),
r AS (
  SELECT DATE(created_at_kst) d, member_id, _rev_pf
  FROM `{RSV}`
  WHERE pf_type='0' AND created_at_kst >= DATETIME '{START}' AND created_at_kst < DATETIME '{end}'
),
acc AS (
  SELECT v.d, v.member_id, MAX(r._rev_pf) rev
  FROM v JOIN r ON v.d=r.d AND v.member_id=r.member_id
  GROUP BY 1,2
)
SELECT (SELECT COUNT(DISTINCT member_id) FROM v) exposed_uu,
       (SELECT SUM(rev) FROM acc) conv_rev,
       (SELECT AVG(rev) FROM acc) conv_avg""")

    # 3) 전체 예약 / 노출된(그날 예약한) 고객 / PF0·30·70 비율 / 단가
    rz = bq(f"""
WITH v AS (
  SELECT DISTINCT DATE(event_at_kst) d, member_id
  FROM `{EVT}`
  WHERE page_name='upsell_bottomsheet' AND event_name='view' AND {PF_FILTER}
    AND event_at_kst >= DATETIME '{START}' AND event_at_kst < DATETIME '{end}'
    AND member_id IS NOT NULL
),
r AS (
  SELECT DATE(created_at_kst) d, member_id, CAST(pf_type AS STRING) pf, _rev_pf
  FROM `{RSV}`
  WHERE created_at_kst >= DATETIME '{START}' AND created_at_kst < DATETIME '{end}'
)
SELECT
  COUNT(*) rsv_total,
  COUNTIF(pf='0') n0, COUNTIF(pf='30') n30, COUNTIF(pf='70') n70,
  AVG(IF(pf='0', _rev_pf, NULL)) avg0,
  AVG(IF(pf='30', _rev_pf, NULL)) avg30
FROM r""")[0]

    # 4) 노출된 고객 중 그날 실제 예약까지 한 (일자·회원) 수 = 예약자 대비 수락률 분모
    re_row = bq(f"""
WITH v AS (
  SELECT DISTINCT DATE(event_at_kst) d, member_id
  FROM `{EVT}`
  WHERE page_name='upsell_bottomsheet' AND event_name='view' AND {PF_FILTER}
    AND event_at_kst >= DATETIME '{START}' AND event_at_kst < DATETIME '{end}'
    AND member_id IS NOT NULL
),
ar AS (
  SELECT DISTINCT DATE(created_at_kst) d, member_id
  FROM `{RSV}`
  WHERE created_at_kst >= DATETIME '{START}' AND created_at_kst < DATETIME '{end}'
)
SELECT COUNT(*) reserved_exposed FROM v JOIN ar USING (d, member_id)""")[0]

    d0 = datetime.strptime("2026-05-21", "%Y-%m-%d").date()
    dN = datetime.strptime(cut, "%Y-%m-%d").date()
    days = [(d0 + timedelta(days=k)).strftime("%Y-%m-%d") for k in range((dN - d0).days + 1)]
    by = {r["dt"]: r for r in daily}
    labels, exposed, accepted = [], [], []
    for i, d in enumerate(days):
        row = by.get(d, {})
        labels.append(datetime.strptime(d, "%Y-%m-%d").strftime("%-m/%-d"))
        exposed.append(int(num(row.get("exposed"))))
        # 최근 2일 수락은 결제 정산 중 → 차트에서 null
        acc = int(num(row.get("accepted")))
        accepted.append(None if i >= len(days) - 2 else acc)

    exposed_cnt = sum(int(num(by.get(d, {}).get("exposed"))) for d in days)
    accepted_pf0 = sum(int(num(by.get(d, {}).get("accepted"))) for d in days)
    t = tot[0]
    exposed_uu = int(num(t.get("exposed_uu")))
    conv_rev = num(t.get("conv_rev"))
    conv_avg = num(t.get("conv_avg"))
    rsv_total = int(num(rz.get("rsv_total")))
    n0, n30, n70 = int(num(rz.get("n0"))), int(num(rz.get("n30"))), int(num(rz.get("n70")))
    reserved_exposed = int(num(re_row.get("reserved_exposed")))
    avg0, avg30 = num(rz.get("avg0")), num(rz.get("avg30"))

    rate_exp = round(accepted_pf0 / exposed_cnt * 100, 1) if exposed_cnt else 0
    rate_rsv = round(accepted_pf0 / reserved_exposed * 100, 1) if reserved_exposed else 0
    expose_pct = round(exposed_cnt / rsv_total * 100, 1) if rsv_total else 0
    stable_cut = datetime.strptime(days[-3], "%Y-%m-%d").strftime("%-m/%-d") if len(days) >= 3 else labels[-1]

    PF = {
        "meta": {"start": "2026-05-21 14:00", "cut": datetime.strptime(cut, "%Y-%m-%d").strftime("%-m/%-d"),
                 "cutFull": cut, "generated": datetime.now(KST).strftime("%Y-%m-%d %H:%M KST")},
        "exposedUU": exposed_uu, "exposedCnt": exposed_cnt, "acceptedPf0": accepted_pf0,
        "rsvTotal": rsv_total, "rateExp": rate_exp, "rateRsv": rate_rsv, "exposePct": expose_pct,
        "revenueEok": round(conv_rev / 1e8, 2), "convAvg": int(round(conv_avg)),
        "avgPf0": int(round(avg0)), "avgPf30": int(round(avg30)),
        "pfRatio": {"p0": round(n0 / rsv_total * 100, 1), "p30": round(n30 / rsv_total * 100, 1),
                    "p70": round(n70 / rsv_total * 100, 1)},
        "stableCut": stable_cut,
        "daily": {"labels": labels, "exposed": exposed, "accepted": accepted},
    }
    with open(a.out, "w") as fp:
        fp.write("window.PF = " + json.dumps(PF, ensure_ascii=False) + ";\n")
    print(f"wrote {a.out} (cut={cut})")
    print(f"  노출고객 {exposed_uu:,} / 노출 {exposed_cnt:,} / 수락 {accepted_pf0:,} / 예약 {rsv_total:,}")
    print(f"  수락률 노출대비 {rate_exp}% 예약자대비 {rate_rsv}% / 노출비율 {expose_pct}%")
    print(f"  매출 {conv_rev/1e8:.2f}억 전환평균 {int(round(conv_avg)):,} / PF0평균 {int(round(avg0)):,} PF30평균 {int(round(avg30)):,}")
    print(f"  비율 PF0 {PF['pfRatio']['p0']}% PF30 {PF['pfRatio']['p30']}% PF70 {PF['pfRatio']['p70']}%")


if __name__ == "__main__":
    main()
