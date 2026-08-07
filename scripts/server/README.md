# 서버 프로비저닝 스크립트

오라클 클라우드에 GA:ON 서버를 새로 세울 때 쓰는 스크립트입니다.
2026-08-07 Micro → A1 이사에서 실제로 쓴 절차를 그대로 옮겼습니다.

배포 자체는 `.github/workflows/cd.yml`이 합니다. 이 스크립트들은 **CD가 성공할 수 있는
빈 서버를 만드는 것**까지만 담당합니다. 코드 전송·`pip install`·서비스 기동은 CD 몫입니다.

## 순서

| | 하는 일 | 어디서 |
|---|---|---|
| 1 | 인스턴스 생성 (Ubuntu 24.04, SSH 공개키 2개 등록) | OCI 콘솔 |
| 2 | Security List에 80·443 ingress 추가 | OCI 콘솔 |
| 3 | `provision_server.sh` | 서버 |
| 4 | `SSH_HOST` 시크릿 변경 → CD 실행 | GitHub |
| 5 | DNS A 레코드 이전 | 도메인 관리 |
| 6 | `enable_https.sh` | 서버 |

```bash
scp scripts/server/provision_server.sh ubuntu@<HOST>:~/
ssh ubuntu@<HOST> 'bash ~/provision_server.sh'
```

SSH 공개키는 **개인 접속용과 배포용 두 개**를 인스턴스 생성 화면에서 함께 넣습니다.
배포용 공개키는 개발 PC의 `~/.ssh/gaon_deploy_key.pub`에 있습니다
(GitHub Secret에는 개인키만 있고 공개키는 볼 수 없습니다).

## 밟은 함정

이사에서 실제로 걸린 것들입니다. 스크립트가 전부 처리하지만, 왜 그런 코드가
들어 있는지 모르면 나중에 지우게 되므로 남겨 둡니다.

### 1. `certbot --redirect`가 배포를 깨뜨린다

`certbot --nginx --redirect`는 80번 server 블록을 이렇게 바꿉니다.

```nginx
if ($host = 도메인) { return 301 https://$host$request_uri; }
return 404;
```

`cd.yml`의 배포 검증은 `http://127.0.0.1/api/health`와 `/api/simulate`를 때리는데,
Host가 `127.0.0.1`이라 리다이렉트 조건에 안 걸리고 **`return 404`로 떨어집니다.**
코드는 멀쩡한데 배포만 매번 실패합니다.

그래서 `enable_https.sh`는 `certonly --webroot`로 인증서만 받고 nginx 설정은 직접 씁니다.

### 2. 방화벽이 두 겹이다

OCI Security List(콘솔)와 인스턴스 iptables 양쪽을 다 열어야 합니다.
한쪽만 열면 "설정은 다 했는데 브라우저가 안 열리는" 상태가 되고 원인이 안 보입니다.

iptables는 **순서**도 중요합니다. 오라클 이미지의 INPUT 체인은 마지막이 REJECT라,
그 아래에 규칙을 넣으면 있어도 무시됩니다. 스크립트는 REJECT 줄 번호를 실행 시점에
찾아서 그 앞에 끼웁니다.

### 3. nginx 기본 사이트를 지워야 한다

우리 블록도 `default_server`라 stock `default`와 공존하면
`duplicate default server`로 nginx가 아예 기동하지 못합니다.

### 4. `systemctl reload nginx`는 즉시 끝나지 않는다

기존 워커가 처리 중인 연결을 마칠 때까지 살아 있습니다. reload 직후에 바로
확인 요청을 보내면 **옛 설정에 걸려 오탐**이 납니다. 검증 함수가 재시도로 감싼 이유입니다.

### 5. DNS를 먼저 옮겨야 인증서가 나온다

Let's Encrypt가 도메인을 조회해서 그 IP의 80번으로 토큰을 받으러 옵니다.
DNS가 아직 옛 서버를 가리키면 무조건 실패합니다. `enable_https.sh`가 시작할 때
공인 IP와 A 레코드를 대조해서 어긋나면 멈춥니다.

### 6. 무료 서브도메인은 인증서 발급 한도를 공유한다

`kro.kr` 같은 무료 서브도메인은 공개 접미사 목록(PSL)에 없어서, Let's Encrypt가
접미사 전체를 하나의 등록 도메인으로 봅니다. **주 50장 한도를 그 서비스 사용자 전원이
나눠 씁니다.** 막히면 `too many certificates already issued for: kro.kr`이 뜹니다.

이건 발급·갱신 시점에만 걸리는 문제이고 방문자 접속과는 무관합니다.
막히면 ZeroSSL 등 다른 무료 ACME CA로 우회합니다(EAB 자격증명 필요).

## 현재 운영 서버와의 차이

지금 도는 서버는 `certbot --nginx`로 발급한 뒤 nginx 설정을 손으로 고친 상태라,
갱신이 **nginx 플러그인** 방식으로 등록돼 있습니다(`renew --dry-run` 통과 확인).
이 스크립트는 새 서버용이며 **webroot** 방식을 씁니다. 둘 다 정상 동작하며,
webroot 쪽이 nginx 설정을 건드리지 않아 함정 1이 재발하지 않습니다.

## 관련 문서

- `../../.github/workflows/cd.yml` — 배포 파이프라인
- `Oracle/GAON_LLM_배포연동_가이드_2026-07-30_ko.md` — 최초 배포·역터널 전체 절차
- `Oracle/2026-08-01_배포_CICD_정리_ko.md` — CI/CD 구성과 설계 근거
