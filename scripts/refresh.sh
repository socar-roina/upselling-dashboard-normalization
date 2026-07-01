#!/bin/bash
# PF 정규화 대시보드 자동 갱신: BQ 재집계 → data.js → 검증(python3) → 변경 시 커밋/푸시
# launchd가 매일 11:00, 15:00(KST) 호출. 11시 성공 시 15시는 마커로 스킵.
REPO="/Users/admin/upselling-work/dashboards/upselling-dashboard-normalization"
LOG="$REPO/scripts/refresh.log"
MARKER="$REPO/scripts/.last_success"
TODAY=$(date +%Y-%m-%d)
export PATH="/opt/homebrew/bin:/usr/local/bin:/Users/admin/.local/bin:/usr/bin:/bin:/usr/sbin:/sbin:$PATH"
export HOME="/Users/admin"
cd "$REPO" || exit 1
if [ "$(cat "$MARKER" 2>/dev/null)" = "$TODAY" ]; then
  echo "$(date '+%F %T') 오늘 이미 갱신됨, 스킵" >> "$LOG"; exit 0
fi
{
  echo "=== $(date '+%F %T') 갱신 시작 ==="
  git pull -q --rebase origin main 2>/dev/null
  if ! python3 scripts/gen_data.py --out data.js; then echo "gen_data.py 실패 → 종료"; exit 1; fi
  if ! python3 -c "import json,re; s=open('data.js').read(); d=json.loads(re.sub(r'^window\.PF\s*=\s*','',s).strip().rstrip(';')); assert d['rsvTotal']>0 and d['exposedCnt']>0 and d['acceptedPf0']>0 and d['meta']['cut'] and len(d['daily']['labels'])>0; print('검증 OK 예약',d['rsvTotal'],'수락',d['acceptedPf0'],'cut',d['meta']['cut'])"; then
    echo "검증 실패 → 커밋 안 함"; exit 1
  fi
  if git diff --quiet data.js; then
    echo "data.js 변경 없음"
  else
    git add data.js
    git commit -q -m "데이터 자동 갱신 (${TODAY})" || { echo "commit 실패"; exit 1; }
    if git push -q origin HEAD; then echo "푸시 완료"; else echo "푸시 실패"; exit 1; fi
  fi
  echo "$TODAY" > "$MARKER"
  echo "=== $(date '+%F %T') 성공 ==="
} >> "$LOG" 2>&1
