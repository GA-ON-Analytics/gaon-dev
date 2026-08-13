from __future__ import annotations

import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from backend import main
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


if __name__ == "__main__":
    unittest.main()
