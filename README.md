# GA:ON

도시 열섬 데이터를 분석하고, 녹지율 등 정책 변수를 조정하여 온도 저감 효과를 확인하는 웹 기반 정책 시뮬레이션 대시보드입니다.

## 기술 구성

- Frontend: React, TypeScript, Vite
- Backend: FastAPI, Python
- Data: CSV, GeoJSON
- Analysis: 머신러닝 예측 및 정책 시뮬레이션

## 프로젝트 구조

```text
gaon-dev/
├── backend/            # FastAPI 서버 및 예측 로직
├── docs/               # 프로젝트 문서
├── public/             # CSV, GeoJSON 등 정적 데이터
├── scripts/            # 데이터 처리 스크립트
├── src/                # React 프론트엔드
├── index.html
├── package.json
├── package-lock.json
└── vite.config.ts
```

---

# 최초 환경 설정

## 1. 프로젝트 Clone

### GitHub Desktop

```text
File
→ Clone Repository
→ GA-ON-Analytics/gaon-dev 선택
→ Clone
```

### 터미널

```bash
git clone https://github.com/GA-ON-Analytics/gaon-dev.git
cd gaon-dev
```

---

## 2. 프론트엔드 환경 설치

Node.js가 설치되어 있어야 합니다.

```bash
npm ci
```

이 명령은 `package-lock.json`을 기준으로 `node_modules`를 로컬에 생성합니다.

프론트엔드 실행:

```bash
npm run dev
```

접속 주소:

```text
http://localhost:5173
```

---

## 3. 백엔드 환경 설치

프로젝트 최상위 폴더에서 Python 가상환경을 생성합니다.

### macOS / Linux

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r backend/requirements.txt
```

### Windows

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r backend/requirements.txt
```

백엔드 실행:

```bash
python -m uvicorn backend.main:app --reload
```

API 주소:

```text
http://localhost:8000
```

API 문서:

```text
http://localhost:8000/docs
```

---

# 프로젝트 실행

프론트엔드와 백엔드는 서로 다른 터미널에서 실행합니다.

## 터미널 1: 백엔드

macOS / Linux:

```bash
source .venv/bin/activate
python -m uvicorn backend.main:app --reload
```

Windows:

```bash
.venv\Scripts\activate
python -m uvicorn backend.main:app --reload
```

## 터미널 2: 프론트엔드

```bash
npm run dev
```

---

# GitHub에 포함되지 않는 파일

다음 파일은 GitHub에 저장하지 않고 각 컴퓨터에서 생성합니다.

```text
node_modules/       npm ci로 생성
.venv/              python -m venv로 생성
dist/               npm run build로 생성
__pycache__/        Python 실행 시 자동 생성
.env                개인 환경변수 및 비밀정보
.DS_Store           macOS 시스템 파일
```

따라서 Clone 직후 `node_modules`와 `.venv`가 없는 것은 정상입니다.

개발 실행에 필요한 파일 생성:

```bash
npm ci
python3 -m venv .venv
source .venv/bin/activate
pip install -r backend/requirements.txt
```

배포용 프론트엔드 파일이 필요한 경우:

```bash
npm run build
```

실행 후 `dist` 폴더가 로컬에 생성됩니다.

---

# 공동 작업 방법

본 프로젝트는 별도의 기능 브랜치를 사용하지 않고 `main` 브랜치에서 공동 작업합니다.

## 작업 시작 전

다른 팀원의 최신 작업 내용을 먼저 받습니다.

### GitHub Desktop

```text
Fetch origin
→ Pull origin
```

### 터미널

```bash
git pull origin main
```

최신 변경사항을 받지 않고 작업하면 충돌하거나 다른 사람의 작업을 덮어쓸 수 있습니다.

## 로컬에서 개발

Clone한 `gaon-dev` 폴더를 VS Code로 열어 수정합니다.

```text
VS Code
→ File
→ Open Folder
→ gaon-dev 선택
```

다음 폴더는 직접 수정하지 않습니다.

```text
node_modules/
.venv/
dist/
__pycache__/
```

이 폴더들은 소스가 아니라 설치 또는 실행 과정에서 자동 생성되는 파일입니다.

## 작업 완료 후

GitHub Desktop에서 변경된 파일을 확인합니다.

```text
Summary 작성
→ Commit to main
→ Push origin
```

커밋 메시지는 변경 내용을 구체적으로 작성합니다.

예시:

```text
Add temperature simulation API
Fix map grid rendering
Update dashboard layout
Add district dataset
Update project documentation
```

터미널을 사용할 경우:

```bash
git add .
git commit -m "변경 내용"
git push origin main
```

---

# 공동 작업 주의사항

1. 작업하기 전에 반드시 `Pull`합니다.
2. 같은 파일을 동시에 수정하지 않도록 담당 작업을 공유합니다.
3. 프로젝트 폴더 전체를 이전 버전으로 덮어쓰지 않습니다.
4. 수정한 파일만 반영합니다.
5. 작업 단위가 끝날 때마다 Commit과 Push를 합니다.
6. `.env`, 비밀번호, API 키는 GitHub에 올리지 않습니다.
7. `node_modules`, `.venv`, `dist`는 Commit하지 않습니다.

예시:

```text
개발자 A: src/ 프론트엔드 수정
개발자 B: backend/ API 수정
```

서로 다른 파일을 수정하면 대부분 문제없이 합쳐집니다.

같은 파일의 같은 부분을 동시에 수정하면 충돌이 발생할 수 있으므로, 작업 전에 팀원에게 수정 범위를 알립니다.

---

# 변경사항 받기

다른 팀원이 Push한 내용을 로컬에 반영하려면:

### GitHub Desktop

```text
Fetch origin
→ Pull origin
```

### 터미널

```bash
git pull origin main
```

`package.json` 또는 `package-lock.json`이 변경된 경우:

```bash
npm ci
```

`backend/requirements.txt`가 변경된 경우:

```bash
source .venv/bin/activate
pip install -r backend/requirements.txt
```

이렇게 하면 새로 추가되거나 변경된 라이브러리가 현재 로컬 환경에 반영됩니다.
