#!/usr/bin/env bash
#
# GA:ON — HTTPS 적용 (Let's Encrypt)
#
# 전제: provision_server.sh 가 끝났고, CD 로 코드가 배포됐고,
#       DNS A 레코드가 이 서버를 가리키고 있어야 한다.
#       DNS 가 아직 옛 서버를 가리키면 인증서 검증이 그쪽으로 가서 반드시 실패한다.
#
# ★ certbot --nginx --redirect 를 쓰지 않는 이유
#
#   그 옵션은 80번 server 블록을 이렇게 바꿔놓는다.
#       if ($host = 도메인) { return 301 https://...; }
#       return 404;                     ← 도메인이 아닌 모든 요청을 버린다
#
#   그러면 cd.yml 의 배포 검증 두 개가 죽는다. 둘 다 Host 가 127.0.0.1 이라
#   리다이렉트 조건에 안 걸리고 404 로 떨어지기 때문이다.
#       curl -sf http://127.0.0.1/api/health
#       curl -s -X POST http://127.0.0.1/api/simulate
#   코드는 멀쩡한데 배포만 매번 실패하는, 원인이 제일 안 보이는 형태가 된다.
#   (2026-08-07 A1 이사에서 실제로 겪었다.)
#
#   그래서 여기서는 certonly --webroot 로 인증서만 받고, nginx 설정은
#   우리가 직접 쓴다. 갱신도 webroot 방식이라 nginx 설정을 건드리지 않는다.
#
# 사용:
#   ssh ubuntu@<HOST> 'bash ~/enable_https.sh'
#   GAON_DOMAIN / GAON_EMAIL 환경변수로 덮어쓸 수 있다.
#
set -euo pipefail

DOMAIN=${GAON_DOMAIN:-ga-on.kro.kr}
EMAIL=${GAON_EMAIL:-jmim613@gmail.com}
WEBROOT=/var/www/html
SITE=/etc/nginx/sites-available/gaon
LIVE=/etc/letsencrypt/live/$DOMAIN

say() { echo ""; echo "===== $* ====="; }

# ---------------------------------------------------------------------------
say "0. 사전 확인"

echo "도메인 $DOMAIN"

# DNS 가 이 서버를 가리키는지 먼저 본다. 아니면 발급이 무조건 실패한다.
MYIP=$(curl -s --max-time 10 https://checkip.amazonaws.com || echo "?")
DNSIP=$(getent hosts "$DOMAIN" | awk '{print $1; exit}' || echo "?")
echo "이 서버의 공인 IP : $MYIP"
echo "$DOMAIN 의 A 레코드: $DNSIP"
if [ "$MYIP" != "$DNSIP" ]; then
    echo ""
    echo "DNS 가 이 서버를 가리키지 않는다. A 레코드를 옮기고 전파를 기다린 뒤 다시 실행할 것."
    echo "  확인:  nslookup $DOMAIN 8.8.8.8"
    exit 1
fi

sudo mkdir -p "$WEBROOT/.well-known/acme-challenge"

# ---------------------------------------------------------------------------
say "1. certbot 설치"

sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq certbot

# ---------------------------------------------------------------------------
say "2. 인증서 발급 (webroot)"

# --keep-until-expiring: 이미 유효한 인증서가 있으면 재발급하지 않는다.
# 재실행해도 Let's Encrypt 발급 한도를 축내지 않게 하는 장치다.
sudo certbot certonly --webroot -w "$WEBROOT" -d "$DOMAIN" \
    --agree-tos --no-eff-email -m "$EMAIL" \
    --non-interactive --keep-until-expiring \
    --deploy-hook "systemctl reload nginx"

sudo test -f "$LIVE/fullchain.pem"
echo "인증서 확인: $LIVE/fullchain.pem"

# 무료 서브도메인(kro.kr 등)은 공개 접미사 목록에 없어서 Let's Encrypt 가
# 그 접미사 전체를 하나의 등록 도메인으로 본다. 주 50장 한도를 남과 나눠 쓰므로
# 'too many certificates already issued for: <접미사>' 가 뜰 수 있다.
# 그때는 ZeroSSL 등 다른 ACME CA 로 우회한다(무료, EAB 자격증명 필요).

# ---------------------------------------------------------------------------
say "3. nginx 설정 재작성"

STAMP=$(date +%Y%m%d-%H%M%S)
sudo cp "$SITE" "$SITE.bak-$STAMP"
echo "백업 $SITE.bak-$STAMP"

sudo tee "$SITE" >/dev/null <<NGINX
# HTTPS — 외부 접속의 정문
server {
    listen 443 ssl default_server;
    listen [::]:443 ssl default_server ipv6only=on;
    server_name $DOMAIN;

    ssl_certificate     $LIVE/fullchain.pem;
    ssl_certificate_key $LIVE/privkey.pem;
    include /etc/letsencrypt/options-ssl-nginx.conf;
    ssl_dhparam /etc/letsencrypt/ssl-dhparams.pem;

    include /etc/nginx/snippets/gaon-app.conf;
}

# HTTP — 도메인 요청만 https 로 넘기고, 나머지는 그대로 서빙한다.
#
# ★ 여기에 'return 404' 를 넣지 말 것. cd.yml 의 배포 검증이
#   http://127.0.0.1/api/health 와 /api/simulate 를 때리는데,
#   Host 가 127.0.0.1 이라 리다이렉트 조건에 안 걸리고 404 로 떨어진다.
server {
    listen 80 default_server;
    listen [::]:80 default_server;
    server_name _;

    # 갱신 챌린지는 리다이렉트보다 앞에 둔다.
    location ^~ /.well-known/acme-challenge/ {
        root $WEBROOT;
        default_type "text/plain";
        try_files \$uri =404;
    }

    if (\$host = $DOMAIN) {
        return 301 https://\$host\$request_uri;
    }

    include /etc/nginx/snippets/gaon-app.conf;
}
NGINX

sudo nginx -t
sudo systemctl reload nginx

# ---------------------------------------------------------------------------
say "4. 검증"

# ★ reload 는 즉시 끝나지 않는다. 기존 워커가 처리 중인 연결을 마칠 때까지
#   살아 있어서, 바로 찌르면 옛 설정에 걸려 오탐이 난다. 재시도로 감싼다.
fail=0
probe() {  # 설명, 기대코드, curl 인자...
    local label="$1" want="$2"; shift 2
    local got=""
    for _ in 1 2 3 4 5; do
        got=$(curl -s -o /dev/null -w '%{http_code}' --max-time 20 "$@" || echo 000)
        [ "$got" = "$want" ] && break
        sleep 1
    done
    if [ "$got" = "$want" ]; then
        echo "  OK   $label ($got)"
    else
        echo "  FAIL $label — 기대 $want, 실제 $got"
        fail=1
    fi
}

probe "127.0.0.1 /api/health (CD 경로)" 200 "http://127.0.0.1/api/health"
probe "http 도메인 → 301"               301 -H "Host: $DOMAIN" "http://127.0.0.1/"
probe "https 도메인 /api/health"         200 "https://$DOMAIN/api/health"
probe "https 도메인 대시보드"             200 "https://$DOMAIN/"

echo ""
echo "--- 모델 검증 (cd.yml 과 같은 요청) ---"
RESULT=$(curl -s --max-time 30 -X POST http://127.0.0.1/api/simulate \
  -H 'Content-Type: application/json' \
  -d '{"grid_id":"11230_00001","changes":{"green_ratio":0.05}}')
echo "$RESULT" | head -c 200; echo ""
if echo "$RESULT" | grep -q '"delta_c"'; then
    echo "  OK   delta_c 있음"
else
    echo "  FAIL delta_c 없음"
    fail=1
fi

# ---------------------------------------------------------------------------
say "5. 갱신 리허설"

# 발급만 되고 갱신이 안 되는 상태를 90일 뒤에 발견하는 게 최악이다.
# 이 단계가 진짜 검증이다.
if sudo certbot renew --dry-run; then
    echo "갱신 리허설 통과"
else
    echo "갱신 리허설 실패 — 90일 뒤 인증서가 만료된다. 원인을 먼저 잡을 것."
    fail=1
fi

echo ""
if [ "$fail" -eq 0 ]; then
    echo "HTTPS 적용 완료 — https://$DOMAIN"
else
    echo "실패 항목이 있다. 되돌리려면:"
    echo "  sudo cp $SITE.bak-$STAMP $SITE && sudo nginx -t && sudo systemctl reload nginx"
    exit 1
fi
