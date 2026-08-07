#!/usr/bin/env bash
#
# GA:ON — 오라클 서버 프로비저닝 (HTTP 까지)
#
# 목적: .github/workflows/cd.yml 의 CD 가 성공할 수 있는 최소 상태까지
#       서버를 만든다. 코드·파이썬 패키지·서비스 기동은 CD 가 하므로
#       여기서 손대지 않는다.
#
# HTTPS 는 이 스크립트 다음에 enable_https.sh 로 붙인다.
# 인증서가 없는 상태에서 443 블록을 쓰면 nginx 가 기동조차 못 하기 때문에
# 두 단계로 나눠 두었다.
#
# 대상: Ubuntu 24.04 (x86_64 / aarch64 공통), 유저 ubuntu
# 성질: 몇 번 실행해도 안전(idempotent)
#
# 사용:
#   scp scripts/server/provision_server.sh ubuntu@<HOST>:~/
#   ssh ubuntu@<HOST> 'bash ~/provision_server.sh'
#
set -euo pipefail

APP_DIR=/srv/gaon
VENV=$APP_DIR/.venv
OWNER=ubuntu
DOMAIN=${GAON_DOMAIN:-ga-on.kro.kr}
WEBROOT=/var/www/html

say() { echo ""; echo "===== $* ====="; }

# ---------------------------------------------------------------------------
say "0. 사전 확인"

if [ "$(whoami)" != "$OWNER" ]; then
    echo "이 스크립트는 $OWNER 유저로 실행해야 한다 (현재: $(whoami))"
    exit 1
fi

# CD 가 'sudo -n systemctl is-active gaon-api' 를 쓴다. -n 은 비밀번호를 물으면
# 즉시 실패하므로 NOPASSWD 가 없으면 배포가 첫 단계에서 죽는다.
if ! sudo -n true 2>/dev/null; then
    echo "sudo NOPASSWD 가 없다. /etc/sudoers.d/90-cloud-init-users 를 확인할 것."
    exit 1
fi
echo "sudo NOPASSWD OK / 아키텍처 $(uname -m) / 도메인 $DOMAIN"

# ---------------------------------------------------------------------------
say "1. 패키지"

sudo apt-get update -qq
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq \
    rsync nginx python3-venv python3-dev curl

# 오라클 Ubuntu 이미지는 netfilter-persistent 를 이미 갖고 있다.
# 없으면 iptables 규칙이 재부팅에서 사라진다.
if ! command -v netfilter-persistent >/dev/null; then
    sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq iptables-persistent
fi

# ---------------------------------------------------------------------------
say "2. 앱 디렉터리"

# 소유자가 ubuntu 여야 한다. CD 의 rsync 는 sudo 없이 쓴다.
sudo mkdir -p "$APP_DIR/backend" "$APP_DIR/dist/dashboard"
sudo chown -R "$OWNER:$OWNER" "$APP_DIR"
sudo mkdir -p "$WEBROOT/.well-known/acme-challenge"
ls -ld "$APP_DIR" "$APP_DIR/backend" "$APP_DIR/dist" "$APP_DIR/dist/dashboard"

# ---------------------------------------------------------------------------
say "3. 파이썬 가상환경 (비어 있어도 된다)"

# CD 의 '파이썬 의존성 설치' 단계가 $VENV/bin/pip 을 찾는다. 패키지 자체는
# CD 가 backend/requirements.txt 로 채우므로 여기서는 껍데기만 만든다.
# scikit-learn 1.9.0 이 Python 3.10+ 를 요구하므로 3.12 여야 한다.
if [ ! -x "$VENV/bin/python" ]; then
    python3 -m venv "$VENV"
fi
"$VENV/bin/pip" install --quiet --upgrade pip
"$VENV/bin/python" -V

# ---------------------------------------------------------------------------
say "4. iptables — 80/443"

# ★ 방화벽은 두 겹이다. 여기서 여는 건 인스턴스 내부일 뿐이고,
#   OCI 콘솔의 Security List 에도 80·443 ingress 규칙이 있어야 한다.
#   한쪽만 열면 "설정은 다 했는데 브라우저가 안 열리는" 상태가 된다.
#
# 오라클 이미지의 INPUT 체인은 마지막이 REJECT 다. 규칙은 위에서부터
# 매칭되므로 REJECT '아래'에 넣으면 규칙이 있어도 무시된다.
for PORT in 80 443; do
    if sudo iptables -C INPUT -m state --state NEW -p tcp --dport "$PORT" -j ACCEPT 2>/dev/null; then
        echo "$PORT 이미 열림"
    else
        LINE=$(sudo iptables -L INPUT --line-numbers | awk '/REJECT/ {print $1; exit}')
        if [ -z "$LINE" ]; then
            sudo iptables -A INPUT -m state --state NEW -p tcp --dport "$PORT" -j ACCEPT
        else
            sudo iptables -I INPUT "$LINE" -m state --state NEW -p tcp --dport "$PORT" -j ACCEPT
        fi
        echo "$PORT 열었다"
    fi
done

sudo netfilter-persistent save >/dev/null
sudo iptables -L INPUT --line-numbers | head -12

# ---------------------------------------------------------------------------
say "5. systemd — gaon-api"

sudo tee /etc/systemd/system/gaon-api.service >/dev/null <<'UNIT'
[Unit]
Description=GA:ON FastAPI (uvicorn)
After=network.target

[Service]
User=ubuntu
WorkingDirectory=/srv/gaon
Environment="PYTHONUNBUFFERED=1"
# LLM 연동용. SSH 역터널이 서버의 127.0.0.1:11434 를 개발 PC로 넘긴다.
# 터널이 없으면 /api/chat 이 503 을 반환하고 나머지 기능은 정상 동작한다.
Environment="OLLAMA_HOST=http://127.0.0.1:11434"
Environment="GAON_LLM_TIMEOUT_SECONDS=120"
# --workers 1 유지. 워커마다 모델을 따로 메모리에 올린다.
ExecStart=/srv/gaon/.venv/bin/uvicorn backend.main:app --host 127.0.0.1 --port 8000 --workers 1
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
UNIT

sudo systemctl daemon-reload
sudo systemctl enable gaon-api >/dev/null 2>&1
echo "유닛 배치 완료 (기동은 CD 가 한다 — 아직 코드가 없다)"

# ---------------------------------------------------------------------------
say "6. nginx (HTTP)"

# 공통 서빙 설정을 snippet 으로 둔다. HTTPS 를 붙이면 80·443 두 블록이
# 이 파일을 함께 include 하므로, 한쪽만 고쳐 어긋나는 사고가 없다.
sudo mkdir -p /etc/nginx/snippets
sudo tee /etc/nginx/snippets/gaon-app.conf >/dev/null <<'SNIPPET'
# GA:ON 공통 서빙 설정 — 80·443 블록이 함께 include 한다.

root /srv/gaon/dist;
index index.html;

# geojson 압축이 이 배포의 핵심. 41MB 파일이 5~8MB로 줄어든다.
# gzip_static: .gz 파일이 있으면 재압축 없이 그대로 보낸다.
gzip on;
gzip_comp_level 5;
gzip_static on;
gzip_min_length 1024;
gzip_proxied any;
gzip_vary on;
gzip_types application/json application/geo+json text/plain
           application/javascript text/css image/svg+xml;

client_max_body_size 4m;

# 대시보드 geojson 을 nginx 가 파일에서 직접 서빙한다.
# 프론트 api.ts 의 fetchJsonWithFallback 이 /api/dashboard/... 실패 시
# /dashboard/... 로 폴백하므로 이 경로가 실제로 쓰인다.
location /dashboard/ {
    expires 7d;
    add_header Cache-Control "public";
    try_files $uri =404;
}

location /api/ {
    proxy_pass http://127.0.0.1:8000;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;

    # LLM 라우팅이 20~30초 걸린다. nginx 기본 60초로는 부족.
    proxy_connect_timeout 10s;
    proxy_send_timeout    180s;
    proxy_read_timeout    180s;
}

# 정적 자산은 해시가 붙어 있어 오래 캐시해도 안전하다.
location /assets/ {
    expires 30d;
    add_header Cache-Control "public, immutable";
    try_files $uri =404;
}

# SPA 라우팅
location / {
    try_files $uri $uri/ /index.html;
}
SNIPPET

# ★ nginx 기본 사이트를 반드시 지운다. 우리 블록도 default_server 라
#   공존하면 'duplicate default server' 로 nginx 가 아예 기동하지 못한다.
sudo rm -f /etc/nginx/sites-enabled/default

sudo tee /etc/nginx/sites-available/gaon >/dev/null <<NGINX
server {
    listen 80 default_server;
    listen [::]:80 default_server;
    server_name $DOMAIN;

    # 인증서 발급·갱신용. 리다이렉트가 생기더라도 이 경로는 그 앞에 둔다.
    location ^~ /.well-known/acme-challenge/ {
        root $WEBROOT;
        default_type "text/plain";
        try_files \$uri =404;
    }

    include /etc/nginx/snippets/gaon-app.conf;
}
NGINX

sudo ln -sf /etc/nginx/sites-available/gaon /etc/nginx/sites-enabled/gaon
sudo nginx -t
sudo systemctl enable --now nginx
sudo systemctl reload nginx

# ---------------------------------------------------------------------------
say "7. 자체 점검"

fail=0
chk() {
    if eval "$2" >/dev/null 2>&1; then
        echo "  OK   $1"
    else
        echo "  FAIL $1"
        fail=1
    fi
}

chk "rsync 설치"             "command -v rsync"
chk "sudo NOPASSWD"          "sudo -n true"
chk "/srv/gaon 소유자 ubuntu" "[ \"\$(stat -c %U $APP_DIR)\" = $OWNER ]"
chk "venv python"            "$VENV/bin/python -V"
chk "venv pip"               "[ -x $VENV/bin/pip ]"
chk "iptables 80"            "sudo iptables -C INPUT -m state --state NEW -p tcp --dport 80 -j ACCEPT"
chk "iptables 443"           "sudo iptables -C INPUT -m state --state NEW -p tcp --dport 443 -j ACCEPT"
chk "nginx 문법"             "sudo nginx -t"
chk "nginx 기동"             "systemctl is-active --quiet nginx"
chk "gaon-api 유닛 등록"      "systemctl cat gaon-api"

echo ""
# 코드가 아직 없으므로 403/404 가 정상이다. 응답 코드가 돌아온다는 것 자체가
# nginx 가 살아 있고 방화벽이 뚫렸다는 뜻. 000 이면 둘 중 하나가 막힌 것이다.
CODE=$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1/ || true)
echo "로컬 HTTP 응답: $CODE  (코드 없으면 403/404 가 정상. 000 이면 nginx 문제)"

echo ""
if [ "$fail" -eq 0 ]; then
    echo "프로비저닝 완료. 다음 순서:"
    echo "  1) OCI 콘솔 Security List 에 80·443 ingress 확인"
    echo "  2) GitHub Secret SSH_HOST 를 이 서버로 지정 후 CD 실행"
    echo "  3) 배포 성공 확인 뒤 DNS A 레코드 이전"
    echo "  4) bash enable_https.sh 로 HTTPS 적용"
else
    echo "실패 항목이 있다. 위 FAIL 줄을 확인할 것."
    exit 1
fi
