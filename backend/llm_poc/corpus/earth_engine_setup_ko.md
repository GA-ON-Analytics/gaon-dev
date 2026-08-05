# Google Earth Engine 설정 순서

## 자동화된 부분

이미 프로젝트에는 아래 자동화가 들어가 있다.

- VWorld 자치구 경계 수집: `src/collect_gu_boundary_vworld.py`
- Earth Engine LST 수집: `src/collect_lst_earth_engine.py`
- 수집된 LST를 ML target으로 연결

## 사용자가 직접 해야 하는 부분

Earth Engine은 API 키가 아니라 Google 계정 인증과 Google Cloud 프로젝트 ID가 필요하다.

1. Earth Engine 등록 페이지에 접속한다.

```text
https://console.cloud.google.com/earth-engine
```

2. Google Cloud 프로젝트를 하나 선택하거나 새로 만든다.

예시 프로젝트 ID:

```text
gaon-earth-engine
```

3. `.env` 파일의 `GOOGLE_CLOUD_PROJECT`에 프로젝트 ID를 넣는다.

주의: 여기에 API 키를 넣으면 안 된다.

```text
GOOGLE_CLOUD_PROJECT=gaon-earth-engine
```

4. 터미널에서 인증한다.

```text
cd C:\Users\ww\Desktop\Oracle\GAON
conda activate gaon-ml
earthengine authenticate
```

5. 자치구 경계를 수집한다.

```text
python src\collect_gu_boundary_vworld.py
```

6. Landsat LST를 수집한다.

```text
python src\collect_lst_earth_engine.py --provider landsat --start-date 2025-06-01 --end-date 2025-09-30
```

7. 시간 모델을 다시 돌린다.

```text
python src\run_gu_time_pipeline.py
```

## 현재 주의할 점

- VWorld에서 `INCORRECT_KEY`가 나오면 VWorld 인증키가 WFS 권한에 맞지 않거나 잘못 들어간 상태다.
- `GOOGLE_CLOUD_PROJECT`는 Google API 키가 아니라 프로젝트 ID다.
- Landsat은 도시 내부 분석에 적합하지만 구름 때문에 날짜별 결측이 생길 수 있다.
- MODIS는 거의 매일 나오지만 1km 해상도라 250m 그리드 분석에는 거칠다.
