# GAON LST target 적용 가이드

## 현재 결정

GAON 머신러닝의 target은 지표면온도(LST, Land Surface Temperature)로 둔다.

이때 LST는 원인 변수가 아니라, 도로율/건물밀도/불투수면/녹지율/NDVI/기상 조건 등이 만든 열환경 결과값으로 본다.

## 모델 구조

입력 변수:

- road_ratio
- building_ratio
- impervious_ratio
- green_ratio
- ndvi
- pop_density
- elderly_ratio
- pm10/pm25
- temp_mean
- humidity_mean
- wind_mean

정답값:

- 구 단위: `target = lst_mean`
- 구-일자 단위: `target = lst_daily`

주의:

- `lst_mean`, `lst_daily`는 정답값이므로 모델 입력 변수에서는 제외한다.
- 정답값을 입력 변수에 넣으면 모델이 답을 보고 맞히는 형태가 되어 가중치 해석이 깨진다.

## 실제 LST 데이터 연결 방식

먼저 서울 자치구 경계가 필요하다.

VWorld 키가 있으면 아래 명령으로 자동 생성한다.

```text
python src/collect_gu_boundary_vworld.py
```

생성 파일:

```text
data/raw/boundary/gu_boundary.geojson
```

일별 LST 파일을 아래 위치에 둔다.

```text
data/raw/lst/gu_daily_lst_manual.csv
```

필수 컬럼:

```text
date,gu_code,gu_name,lst_daily
```

그 다음 실행:

```text
python src/collect_lst_manual.py
python src/run_gu_time_pipeline.py
```

구 평균 LST만 있을 경우:

```text
data/raw/lst/gu_lst_manual.csv
```

필수 컬럼:

```text
gu_code,gu_name,lst_mean
```

그 다음 실행:

```text
python src/collect_lst_manual.py
python src/run_gu_pipeline.py
```

## 다음 고도화

1. Landsat 또는 MODIS 기반 실제 LST를 확보한다.
2. 서울 25개 구 단위로 평균 LST를 집계한다.
3. 시간 모델에서는 날짜별 LST를 집계한다.
4. 송파구 등 후보 구를 선택한 뒤 250m 그리드 단위로 같은 구조를 반복한다.
