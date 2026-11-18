#!/usr/bin/env bash
# מנהרה (SOCKS5 על פורט 1080) לשרת בישראל. מנסה את השרת הראשי ואז את הגיבוי (IL2_SSH_HOST).
# מדפיס אבחון בלי לחשוף סודות. מצליח => IL_PROXY נכתב ל-GITHUB_ENV.
# 1) Tailscale (אם הוגדר): השרת בישראל כ-Exit Node, רק דרך פרוקסי מקומי 1055 - בלי פורט פתוח בשרת
if [ -n "$TS_EXIT" ]; then
  for i in $(seq 1 10); do
    C=$(curl -s -m 15 --socks5-hostname 127.0.0.1:1055 https://ipinfo.io/json | tr -d '\n')
    case "$C" in *'"country": "IL"'*|*'"country":"IL"'*) break;; esac; sleep 3
  done
  echo "::notice title=TUNNEL_TS::$(printf '%s' "$C" | grep -o '"country": *"[A-Z]*"')"
  case "$C" in *'"IL"'*)
    echo "IL_PROXY=socks5://127.0.0.1:1055" >> "$GITHUB_ENV"
    # אבחון: חיבור ישיר או דרך ממסר (relay = איטי), ומהירות הורדה דרך השרת
    exit 0;; esac
  echo "::warning title=TUNNEL_TS_FAIL::tailscale exit node not working - trying SSH"
fi
# 2) SSH (הדרך הישנה)
if [ -z "$IL_SSH_KEY" ] || { [ -z "$IL_SSH_HOST" ] && [ -z "$IL2_SSH_HOST" ]; }; then
  echo "::notice title=TUNNEL::no Israeli server configured - running from GitHub (US)"; exit 0
fi
printf '%s\n' "$IL_SSH_KEY" | tr -d '\r' > /tmp/il_key && chmod 600 /tmp/il_key
U=$(printf '%s' "${IL_SSH_USER:-tunnel}" | tr -d ' \r\n')
n=0
for RAW in "$IL_SSH_HOST" "$IL2_SSH_HOST"; do
  n=$((n+1)); H=$(printf '%s' "$RAW" | tr -d ' \r\n'); [ -z "$H" ] && continue
  if ssh -i /tmp/il_key -o BatchMode=yes -o ConnectTimeout=15 -o StrictHostKeyChecking=accept-new -o ServerAliveInterval=30 -f -N -D 1080 "$U@$H" 2>/tmp/ssh_err; then
    sleep 3
    echo "IL_PROXY=socks5://127.0.0.1:1080" >> "$GITHUB_ENV"
    echo "::notice title=TUNNEL::server $n ok"
    rm -f /tmp/il_key; exit 0
  fi
  echo "::warning title=TUNNEL_FAIL$n::$(tr '\n' ' ' < /tmp/ssh_err | cut -c1-300)"
done
rm -f /tmp/il_key
exit 1
