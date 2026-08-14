from __future__ import annotations

import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from backend import main
from backend.ml import predict_core
from backend.simulation_scope import _geometry_centroid, load_grid_spatial_index


REGULAR_CENTER_GRID_ID = "11110_00142"
CROSS_GU_CENTER_GRID_ID = "11110_00001"
SEOUL_EDGE_CENTER_GRID_ID = "11500_01758"


class FakePredictCore:
    def __init__(
        self,
        *,
        failed_grid_ids: set[str] | None = None,
        raised_grid_ids: set[str] | None = None,
    ):
        self.failed_grid_ids = failed_grid_ids or set()
        self.raised_grid_ids = raised_grid_ids or set()
        self.calls: list[tuple[str, dict[str, float], bool]] = []

    def predict(
        self,
        grid_id: str,
        changes: dict[str, float],
        couple_land_cover: bool = True,
    ) -> dict:
        self.calls.append((grid_id, dict(changes), couple_land_cover))
        if grid_id in self.raised_grid_ids:
            raise RuntimeError("test prediction failure")
        if grid_id in self.failed_grid_ids:
            return {"error": "필수 모델 입력값 누락", "grid_id": grid_id}

        warning = ["학습범위 상한에서 보정(clip)했습니다."] if len(self.calls) == 1 else []
        return {
            "grid_id": grid_id,
            "before_anomaly": 1.0,
            "after_anomaly": 0.8,
            "delta_c": -0.2,
            "changed_features": {
                feature: {"before": 0.2, "after": 0.2 + delta}
                for feature, delta in changes.items()
            },
            "message": "ML simulation completed",
            "warnings": warning,
        }


class FakeBatchPredictCore(FakePredictCore):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.batch_calls: list[tuple[list[str], dict[str, float], bool]] = []

    def predict_batch(
        self,
        grid_ids: list[str],
        changes: dict[str, float],
        couple_land_cover: bool = True,
    ) -> list[dict]:
        self.batch_calls.append((list(grid_ids), dict(changes), couple_land_cover))
        return [
            self.predict(grid_id, changes, couple_land_cover)
            for grid_id in grid_ids
        ]


class SimulationBatchApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.client = TestClient(main.app)
        cls.index = load_grid_spatial_index(str(main.DASHBOARD_100M_MAP_PATH))

    def post_batch(self, payload: dict, fake: FakePredictCore):
        with (
            patch("backend.main._simulation_ready", return_value=True),
            patch("backend.main._load_predict_core", return_value=fake),
        ):
            return self.client.post("/api/simulate/batch", json=payload)

    def test_regular_scope_100_300_500(self) -> None:
        expected_counts = {100: 1, 300: 9, 500: 25}
        changes = {"green_ratio": 0.05}

        for scope_m, expected_count in expected_counts.items():
            with self.subTest(scope_m=scope_m):
                fake = FakePredictCore()
                response = self.post_batch(
                    {
                        "grid_id": REGULAR_CENTER_GRID_ID,
                        "scope_m": scope_m,
                        "changes": changes,
                        "couple_land_cover": False,
                    },
                    fake,
                )
                self.assertEqual(response.status_code, 200)
                payload = response.json()
                self.assertEqual(payload["grid_count"], expected_count)
                self.assertEqual(payload["success_count"], expected_count)
                self.assertEqual(payload["failed_count"], 0)
                self.assertEqual(len(payload["results"]), expected_count)
                self.assertEqual(payload["aggregation"], "area_weighted")
                self.assertNotIn("gu_code", payload)
                self.assertNotIn("scope_mode", payload)
                self.assertNotIn("compact", payload)
                self.assertTrue(all(call[1] == changes for call in fake.calls))
                self.assertTrue(all(call[2] is False for call in fake.calls))

    def test_centroid_is_numerically_stable_at_seoul_coordinates(self) -> None:
        longitude, latitude = _geometry_centroid(
            {
                "type": "Polygon",
                "coordinates": [[
                    [127.0, 37.5],
                    [127.001, 37.5],
                    [127.001, 37.501],
                    [127.0, 37.501],
                    [127.0, 37.5],
                ]],
            }
        )
        self.assertAlmostEqual(longitude, 127.0005, places=9)
        self.assertAlmostEqual(latitude, 37.5005, places=9)

    def test_scope_100_matches_single_predict_contract(self) -> None:
        changes = {"ndvi": 0.03}
        single_fake = FakePredictCore()
        with (
            patch("backend.main._simulation_ready", return_value=True),
            patch("backend.main._load_predict_core", return_value=single_fake),
        ):
            single = self.client.post(
                "/api/simulate",
                json={
                    "grid_id": REGULAR_CENTER_GRID_ID,
                    "changes": changes,
                    "couple_land_cover": False,
                },
            ).json()

        batch_fake = FakePredictCore()
        batch = self.post_batch(
            {
                "grid_id": REGULAR_CENTER_GRID_ID,
                "scope_m": 100,
                "changes": changes,
                "couple_land_cover": False,
            },
            batch_fake,
        ).json()["results"][0]

        for field in ("grid_id", "before_anomaly", "after_anomaly", "delta_c", "changed_features"):
            self.assertEqual(batch[field], single[field])

    def test_invalid_scope_and_missing_center_grid(self) -> None:
        invalid = self.post_batch(
            {"grid_id": REGULAR_CENTER_GRID_ID, "scope_m": 200},
            FakePredictCore(),
        )
        self.assertEqual(invalid.status_code, 422)

        missing = self.post_batch(
            {"grid_id": "99999_99999", "scope_m": 500},
            FakePredictCore(),
        )
        self.assertEqual(missing.status_code, 404)

    def test_gu_boundary_crossing_and_seoul_boundary(self) -> None:
        cross_gu_cells = self.index.select_scope(CROSS_GU_CENTER_GRID_ID, 500)
        self.assertGreater(len({cell.gu_code for cell in cross_gu_cells}), 1)
        cross_response = self.post_batch(
            {"grid_id": CROSS_GU_CENTER_GRID_ID, "scope_m": 500},
            FakePredictCore(),
        ).json()
        self.assertEqual(cross_response["grid_count"], len(cross_gu_cells))

        edge_cells = self.index.select_scope(SEOUL_EDGE_CENTER_GRID_ID, 500)
        self.assertLess(len(edge_cells), 25)
        edge_response = self.post_batch(
            {"grid_id": SEOUL_EDGE_CENTER_GRID_ID, "scope_m": 500},
            FakePredictCore(),
        ).json()
        self.assertEqual(edge_response["grid_count"], len(edge_cells))
        self.assertAlmostEqual(
            edge_response["total_area_m2"],
            sum(cell.area_m2 for cell in edge_cells),
            places=2,
        )

    def test_partial_failure_warning_and_summary(self) -> None:
        target_cells = self.index.select_scope(REGULAR_CENTER_GRID_ID, 300)
        failed_grid_id = target_cells[-1].grid_id
        raised_grid_id = target_cells[-2].grid_id
        fake = FakePredictCore(
            failed_grid_ids={failed_grid_id},
            raised_grid_ids={raised_grid_id},
        )
        response = self.post_batch(
            {
                "grid_id": REGULAR_CENTER_GRID_ID,
                "scope_m": 300,
                "changes": {"albedo": 0.02},
            },
            fake,
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["requested_grid_count"], 9)
        self.assertEqual(payload["success_count"], 7)
        self.assertEqual(payload["failed_count"], 2)
        self.assertEqual(payload["improved_grid_count"], 7)
        self.assertEqual(payload["worsened_grid_count"], 0)
        self.assertEqual(payload["unchanged_grid_count"], 0)
        self.assertEqual(payload["clipped_count"], 1)
        self.assertEqual(
            sum(result["status"] == "failed" for result in payload["results"]),
            2,
        )
        self.assertTrue(any(result.get("warnings") for result in payload["results"]))

    def test_explicit_grid_ids_contract_stays_supported(self) -> None:
        grid_ids = [REGULAR_CENTER_GRID_ID, CROSS_GU_CENTER_GRID_ID]
        response = self.post_batch(
            {"grid_ids": grid_ids, "changes": {"green_ratio": 0.05}},
            FakePredictCore(),
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["target_mode"], "explicit_grid_ids")
        self.assertEqual(payload["count"], 2)
        self.assertEqual(payload["mean_delta_c"], -0.2)
        self.assertEqual(payload["aggregation"], "unweighted")
        self.assertNotIn("compact", payload)

    def test_district_selector_uses_map_gu_code_and_vectorized_batch(self) -> None:
        gu_code = "11110"
        district_cells = self.index.select_district(gu_code)
        changes = {"green_ratio": 0.05}
        fake = FakeBatchPredictCore()
        response = self.post_batch(
            {
                "gu_code": gu_code,
                "scope_mode": "district",
                "changes": changes,
                "couple_land_cover": False,
                "compact": True,
            },
            fake,
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        expected_ids = [cell.grid_id for cell in district_cells]
        self.assertEqual(payload["target_mode"], "district")
        self.assertEqual(payload["gu_code"], gu_code)
        self.assertEqual(payload["scope_mode"], "district")
        self.assertTrue(payload["compact"])
        self.assertEqual(payload["grid_count"], len(district_cells))
        self.assertEqual([result["grid_id"] for result in payload["results"]], expected_ids)
        self.assertEqual(fake.batch_calls, [(expected_ids, changes, False)])

    def test_district_invalid_gu_code_and_selector_conflicts(self) -> None:
        malformed = self.post_batch(
            {"gu_code": "1168", "scope_mode": "district"},
            FakePredictCore(),
        )
        self.assertEqual(malformed.status_code, 422)

        missing = self.post_batch(
            {"gu_code": "99999", "scope_mode": "district"},
            FakePredictCore(),
        )
        self.assertEqual(missing.status_code, 404)

        incomplete = self.post_batch(
            {"gu_code": "11110"},
            FakePredictCore(),
        )
        self.assertEqual(incomplete.status_code, 422)

        conflicts = (
            {
                "grid_ids": [REGULAR_CENTER_GRID_ID],
                "gu_code": "11110",
                "scope_mode": "district",
            },
            {
                "grid_id": REGULAR_CENTER_GRID_ID,
                "scope_m": 500,
                "gu_code": "11110",
                "scope_mode": "district",
            },
        )
        for request in conflicts:
            with self.subTest(request=request):
                response = self.post_batch(request, FakePredictCore())
                self.assertEqual(response.status_code, 422)

    def test_district_compact_fields_summary_and_partial_failure(self) -> None:
        gu_code = "11110"
        cells = self.index.select_district(gu_code)
        failed_grid_id = cells[-1].grid_id
        fake = FakeBatchPredictCore(failed_grid_ids={failed_grid_id})
        response = self.post_batch(
            {
                "gu_code": gu_code,
                "scope_mode": "district",
                "changes": {"albedo": 0.02},
                "compact": True,
            },
            fake,
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["grid_count"], len(cells))
        self.assertEqual(payload["success_count"], len(cells) - 1)
        self.assertEqual(payload["failed_count"], 1)
        self.assertEqual(
            payload["improved_grid_count"]
            + payload["unchanged_grid_count"]
            + payload["worsened_grid_count"],
            payload["success_count"],
        )
        success = next(result for result in payload["results"] if result["status"] == "success")
        failed = next(result for result in payload["results"] if result["status"] == "failed")
        self.assertEqual(set(success), {"grid_id", "status", "delta_c", "area_m2"})
        self.assertEqual(set(failed), {"grid_id", "status", "error"})
        self.assertEqual(failed["grid_id"], failed_grid_id)
        self.assertEqual(payload["aggregation"], "area_weighted")
        self.assertIsNotNone(payload["total_area_m2"])

    def test_compact_is_rejected_for_existing_selectors(self) -> None:
        requests = (
            {"grid_ids": [REGULAR_CENTER_GRID_ID], "compact": True},
            {
                "grid_id": REGULAR_CENTER_GRID_ID,
                "scope_m": 100,
                "compact": True,
            },
        )
        for request in requests:
            with self.subTest(request=request):
                response = self.post_batch(request, FakePredictCore())
                self.assertEqual(response.status_code, 422)

    def test_summary_uses_successful_grid_area_weights(self) -> None:
        summary = main._batch_summary(
            [("a", 2_500.0), ("b", 7_500.0), ("failed", 10_000.0)],
            [
                {"grid_id": "a", "before_anomaly": 0.0, "after_anomaly": -0.2, "delta_c": -0.2},
                {"grid_id": "b", "before_anomaly": 2.0, "after_anomaly": 2.2, "delta_c": 0.2},
                {"grid_id": "failed", "error": "missing"},
            ],
        )
        self.assertEqual(summary["success_count"], 2)
        self.assertEqual(summary["failed_count"], 1)
        self.assertEqual(summary["mean_before_anomaly"], 1.5)
        self.assertEqual(summary["mean_after_anomaly"], 1.6)
        self.assertEqual(summary["mean_delta_c"], 0.1)
        self.assertEqual(summary["successful_area_m2"], 10_000.0)


class VectorizedPredictionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        _, feats, static, _ = predict_core._load()
        valid = static.dropna(subset=feats)
        # 자치구를 건너뛰 실제 grid로 lookup/feature order까지 검증한다.
        cls.grid_ids = (
            valid.groupby("gu_code", sort=True)
            .head(1)["grid_id"]
            .astype(str)
            .tolist()[:12]
        )
        cls.clip_grid_id = str(valid.loc[valid["green_ratio"].idxmax(), "grid_id"])
        cls.client = TestClient(main.app)

    def assert_batch_matches_single(
        self,
        grid_ids: list[str],
        changes: dict[str, float],
        *,
        couple_land_cover: bool,
    ) -> None:
        expected = [
            predict_core.predict(
                grid_id,
                changes,
                couple_land_cover=couple_land_cover,
            )
            for grid_id in grid_ids
        ]
        actual = predict_core.predict_batch(
            grid_ids,
            changes,
            couple_land_cover=couple_land_cover,
        )
        self.assertEqual(actual, expected)

    def test_multiple_grids_and_policies_match_single_exactly(self) -> None:
        cases = (
            ({"albedo": 0.04}, False),
            ({"green_ratio": 0.05}, True),
            ({"green_ratio": 0.1, "impervious_ratio": -0.1, "ndvi": 0.05}, False),
        )
        for changes, couple in cases:
            with self.subTest(changes=changes, couple=couple):
                self.assert_batch_matches_single(
                    self.grid_ids,
                    changes,
                    couple_land_cover=couple,
                )

    def test_clipping_warning_and_changed_features_match_single(self) -> None:
        self.assert_batch_matches_single(
            [self.clip_grid_id],
            {"green_ratio": 0.5},
            couple_land_cover=True,
        )
        result = predict_core.predict_batch(
            [self.clip_grid_id],
            {"green_ratio": 0.5},
            couple_land_cover=True,
        )[0]
        self.assertTrue(any("clip" in warning for warning in result["warnings"]))
        self.assertIn("green_ratio", result["changed_features"])

    def test_invalid_grid_is_partial_failure(self) -> None:
        grid_ids = [self.grid_ids[0], "99999_99999", self.grid_ids[1]]
        actual = predict_core.predict_batch(
            grid_ids,
            {"ndvi": 0.02},
            couple_land_cover=False,
        )
        self.assertNotIn("error", actual[0])
        self.assertIn("error", actual[1])
        self.assertNotIn("error", actual[2])
        self.assertEqual(
            actual,
            [
                predict_core.predict(
                    grid_id,
                    {"ndvi": 0.02},
                    couple_land_cover=False,
                )
                for grid_id in grid_ids
            ],
        )

    def test_vectorized_and_legacy_summary_are_identical(self) -> None:
        targets = [
            (grid_id, float(index + 1) * 10_000.0)
            for index, grid_id in enumerate(self.grid_ids)
        ]
        changes = {"green_ratio": 0.05}
        legacy = [
            predict_core.predict(grid_id, changes, couple_land_cover=True)
            for grid_id, _ in targets
        ]
        vectorized = predict_core.predict_batch(
            [grid_id for grid_id, _ in targets],
            changes,
            couple_land_cover=True,
        )
        self.assertEqual(
            main._batch_summary(targets, vectorized),
            main._batch_summary(targets, legacy),
        )

    def test_real_single_and_batch_api_contracts(self) -> None:
        changes = {"green_ratio": 0.05}
        single = self.client.post(
            "/api/simulate",
            json={
                "grid_id": self.grid_ids[0],
                "changes": changes,
                "couple_land_cover": True,
            },
        )
        self.assertEqual(single.status_code, 200)
        self.assertEqual(
            single.json(),
            predict_core.predict(self.grid_ids[0], changes, couple_land_cover=True),
        )

        requested_ids = [self.grid_ids[0], "99999_99999", self.grid_ids[1]]
        batch = self.client.post(
            "/api/simulate/batch",
            json={
                "grid_ids": requested_ids,
                "changes": changes,
                "couple_land_cover": True,
            },
        )
        self.assertEqual(batch.status_code, 200)
        payload = batch.json()
        self.assertEqual(payload["grid_count"], 3)
        self.assertEqual(payload["success_count"], 2)
        self.assertEqual(payload["failed_count"], 1)
        self.assertEqual(
            [result["grid_id"] for result in payload["results"]],
            requested_ids,
        )
        expected = predict_core.predict_batch(
            requested_ids,
            changes,
            couple_land_cover=True,
        )
        for grid_id, actual_result, expected_result in zip(
            requested_ids, payload["results"], expected
        ):
            normalized_expected = {**expected_result}
            normalized_expected.setdefault("grid_id", grid_id)
            self.assertEqual(
                {key: value for key, value in actual_result.items()
                 if key not in {"area_m2", "status"}},
                normalized_expected,
            )


if __name__ == "__main__":
    unittest.main()
