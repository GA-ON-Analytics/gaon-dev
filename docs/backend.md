# GA_ON FastAPI Backend

## Frontend

```bash
npm install
npm run dev
```

Open:

```text
http://localhost:5173
```

## Backend

```bash
python3 -m pip install -r backend/requirements.txt
python3 -m uvicorn backend.main:app --reload --port 8000
```

## API Checks

```text
http://localhost:8000/api/health
http://localhost:8000/api/seoul-gu
http://localhost:8000/api/grids/11110
```

## ML TODO

- Keep `backend/models/seoul_grid_explain_model.joblib`
- Keep `backend/models/seoul_grid_feature_columns.json`
- Keep `backend/models/feature_meta.json`
- Keep `backend/data/processed/seoul_grid_dataset.csv`
- `/api/simulate` returns real `model.predict()` results only when all required files are present.
- If any required artifact is missing, `/api/simulate` returns `501 not_connected`.

No fake ML values are generated.

## Dashboard Data

`dashboard.zip` is installed with:

```bash
python3 scripts/install-dashboard-data.py ~/Downloads/dashboard.zip
```

The current map reads 100m district files through `/api/grids/{gu_code}` from:

```text
public/dashboard/100m/{gu_code}_{gu_name}.geojson
```

The full dashboard outputs are stored under:

```text
public/dashboard/
```

## ML Artifacts

The compact model is stored at:

```text
backend/models/seoul_grid_explain_model.joblib
```

Prediction remains disabled until all required model inputs are present:

```text
backend/models/seoul_grid_feature_columns.json
backend/models/feature_meta.json
backend/data/processed/seoul_grid_dataset.csv
```

Confirm the scikit-learn version used to save the joblib model with the ML developer before enabling production prediction. Version mismatch warnings should be documented, not ignored.

## Verification

```bash
python3 -m py_compile backend/main.py backend/ml/predict_core.py
npx tsc -b
npm run build
```
