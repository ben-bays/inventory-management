"""
Tests for restocking API endpoints.
"""
import pytest

import mock_data


@pytest.fixture(autouse=True)
def clear_restock_orders():
    """Reset the in-memory restock order list so each test runs in isolation."""
    mock_data.restock_orders.clear()
    yield
    mock_data.restock_orders.clear()


@pytest.fixture
def candidates(client):
    """The current restock candidate list."""
    response = client.get("/api/restock/candidates")
    assert response.status_code == 200
    return response.json()


class TestRestockCandidatesEndpoint:
    """Test suite for the restock candidates endpoint."""

    def test_get_all_restock_candidates(self, client):
        """Test getting all restock candidates."""
        response = client.get("/api/restock/candidates")
        assert response.status_code == 200

        data = response.json()
        assert isinstance(data, list)
        assert len(data) > 0

        first_candidate = data[0]
        assert "id" in first_candidate
        assert "sku" in first_candidate
        assert "name" in first_candidate
        assert "category" in first_candidate
        assert "trend" in first_candidate
        assert "period" in first_candidate
        assert "forecasted_demand" in first_candidate
        assert "quantity_on_hand" in first_candidate
        assert "shortfall" in first_candidate
        assert "unit_cost" in first_candidate
        assert "lead_time_days" in first_candidate
        assert "full_coverage_cost" in first_candidate

    def test_candidate_field_types(self, candidates):
        """Test that candidate numeric fields have proper types."""
        for candidate in candidates:
            assert isinstance(candidate["forecasted_demand"], int)
            assert isinstance(candidate["quantity_on_hand"], int)
            assert isinstance(candidate["shortfall"], int)
            assert isinstance(candidate["unit_cost"], (int, float))
            assert isinstance(candidate["lead_time_days"], int)
            assert isinstance(candidate["full_coverage_cost"], (int, float))

    def test_candidates_are_understocked(self, candidates):
        """Test that every candidate is short of its forecasted demand."""
        for candidate in candidates:
            assert candidate["shortfall"] > 0
            assert candidate["unit_cost"] > 0
            assert (
                candidate["shortfall"]
                == candidate["forecasted_demand"] - candidate["quantity_on_hand"]
            )

    def test_candidates_exclude_sufficiently_stocked_items(self, client, candidates):
        """Test that forecast items stocked at or above demand are excluded."""
        demand_response = client.get("/api/demand")
        forecasts = demand_response.json()

        # MTR-304 is forecast at 35 against 48 on hand - it needs no restock
        forecast_skus = {f["item_sku"] for f in forecasts}
        candidate_skus = {c["sku"] for c in candidates}

        assert "MTR-304" in forecast_skus
        assert "MTR-304" not in candidate_skus

    def test_candidates_sorted_by_shortfall_descending(self, candidates):
        """Test that the worst shortfall is ranked first."""
        shortfalls = [c["shortfall"] for c in candidates]
        assert shortfalls == sorted(shortfalls, reverse=True)

    def test_full_coverage_cost_calculation(self, candidates):
        """Test that full coverage cost equals shortfall times unit cost."""
        for candidate in candidates:
            expected = candidate["shortfall"] * candidate["unit_cost"]
            assert abs(candidate["full_coverage_cost"] - expected) < 0.01

    def test_lead_time_matches_category(self, candidates):
        """Test that lead time comes from the category lookup table."""
        expected_by_category = {
            "Circuit Boards": 21,
            "Sensors": 14,
            "Controllers": 28,
            "Power Supplies": 18,
            "Actuators": 30,
        }

        for candidate in candidates:
            expected = expected_by_category.get(candidate["category"], 21)
            assert candidate["lead_time_days"] == expected

    def test_candidate_on_hand_prefers_inventory(self, client, candidates):
        """Test that on-hand quantity comes from inventory when the SKU exists there."""
        inventory = client.get("/api/inventory").json()
        stock_by_sku = {}
        for item in inventory:
            stock_by_sku[item["sku"]] = stock_by_sku.get(item["sku"], 0) + item["quantity_on_hand"]

        for candidate in candidates:
            if candidate["sku"] in stock_by_sku:
                assert candidate["quantity_on_hand"] == stock_by_sku[candidate["sku"]]


class TestRestockOrdersEndpoint:
    """Test suite for submitting and retrieving restocking orders."""

    def test_get_restock_orders_empty_initially(self, client):
        """Test that no restocking orders exist before any are submitted."""
        response = client.get("/api/restock-orders")
        assert response.status_code == 200
        assert response.json() == []

    def test_create_restock_order(self, client, candidates):
        """Test submitting a restocking order."""
        candidate = candidates[0]
        response = client.post(
            "/api/restock-orders",
            json={
                "budget": 25000,
                "items": [{"sku": candidate["sku"], "quantity": 10}],
            },
        )
        assert response.status_code == 201

        order = response.json()
        assert order["order_number"].startswith("RSO-")
        assert order["status"] == "Submitted"
        assert order["budget"] == 25000
        assert len(order["items"]) == 1

        line = order["items"][0]
        assert line["sku"] == candidate["sku"]
        assert line["quantity"] == 10
        assert line["unit_cost"] == candidate["unit_cost"]
        assert abs(line["line_total"] - 10 * candidate["unit_cost"]) < 0.01

    def test_created_order_appears_in_list(self, client, candidates):
        """Test that a submitted order is returned by the list endpoint."""
        created = client.post(
            "/api/restock-orders",
            json={"budget": 5000, "items": [{"sku": candidates[0]["sku"], "quantity": 5}]},
        ).json()

        response = client.get("/api/restock-orders")
        assert response.status_code == 200

        orders = response.json()
        assert len(orders) == 1
        assert orders[0]["order_number"] == created["order_number"]

    def test_order_total_cost_calculation(self, client, candidates):
        """Test that total cost is the sum of the line totals."""
        items = [{"sku": c["sku"], "quantity": 4} for c in candidates[:3]]
        order = client.post(
            "/api/restock-orders", json={"budget": 50000, "items": items}
        ).json()

        calculated_total = sum(line["line_total"] for line in order["items"])
        assert abs(order["total_cost"] - calculated_total) < 0.01

    def test_order_lead_time_is_slowest_item(self, client, candidates):
        """Test that the order lead time is the maximum across its items."""
        items = [{"sku": c["sku"], "quantity": 2} for c in candidates[:4]]
        order = client.post(
            "/api/restock-orders", json={"budget": 50000, "items": items}
        ).json()

        expected = max(line["lead_time_days"] for line in order["items"])
        assert order["lead_time_days"] == expected

    def test_order_expected_delivery_matches_lead_time(self, client, candidates):
        """Test that expected delivery is the submitted date plus the lead time."""
        from datetime import datetime

        order = client.post(
            "/api/restock-orders",
            json={"budget": 5000, "items": [{"sku": candidates[0]["sku"], "quantity": 1}]},
        ).json()

        submitted = datetime.fromisoformat(order["submitted_date"])
        delivery = datetime.fromisoformat(order["expected_delivery"])
        assert (delivery - submitted).days == order["lead_time_days"]

    def test_order_numbers_increment(self, client, candidates):
        """Test that each submitted order gets a new sequential order number."""
        payload = {"budget": 5000, "items": [{"sku": candidates[0]["sku"], "quantity": 1}]}

        first = client.post("/api/restock-orders", json=payload).json()
        second = client.post("/api/restock-orders", json=payload).json()

        assert first["order_number"] != second["order_number"]
        assert first["id"] == "1"
        assert second["id"] == "2"

    def test_restock_orders_sorted_newest_first(self, client, candidates):
        """Test that the list endpoint returns the newest order first."""
        payload = {"budget": 5000, "items": [{"sku": candidates[0]["sku"], "quantity": 1}]}
        client.post("/api/restock-orders", json=payload)
        second = client.post("/api/restock-orders", json=payload).json()

        orders = client.get("/api/restock-orders").json()
        assert orders[0]["order_number"] == second["order_number"]

    def test_server_ignores_client_supplied_price(self, client, candidates):
        """Test that unit cost comes from the server, not the request body."""
        candidate = candidates[0]
        order = client.post(
            "/api/restock-orders",
            json={
                "budget": 5000,
                "items": [
                    {"sku": candidate["sku"], "quantity": 1, "unit_cost": 0.01}
                ],
            },
        ).json()

        assert order["items"][0]["unit_cost"] == candidate["unit_cost"]

    def test_create_restock_order_with_no_items(self, client):
        """Test that an order with no items is rejected."""
        response = client.post("/api/restock-orders", json={"budget": 1000, "items": []})
        assert response.status_code == 400

        data = response.json()
        assert "detail" in data
        assert "at least one item" in data["detail"].lower()

    def test_create_restock_order_with_invalid_sku(self, client):
        """Test that an unknown SKU is rejected."""
        response = client.post(
            "/api/restock-orders",
            json={"budget": 1000, "items": [{"sku": "NOT-A-SKU", "quantity": 5}]},
        )
        assert response.status_code == 400

        data = response.json()
        assert "detail" in data
        assert "not a valid restock candidate" in data["detail"].lower()

    def test_create_restock_order_with_zero_quantity(self, client, candidates):
        """Test that a zero quantity is rejected."""
        response = client.post(
            "/api/restock-orders",
            json={"budget": 1000, "items": [{"sku": candidates[0]["sku"], "quantity": 0}]},
        )
        assert response.status_code == 400

        data = response.json()
        assert "greater than zero" in data["detail"].lower()

    def test_create_restock_order_with_negative_quantity(self, client, candidates):
        """Test that a negative quantity is rejected."""
        response = client.post(
            "/api/restock-orders",
            json={"budget": 1000, "items": [{"sku": candidates[0]["sku"], "quantity": -5}]},
        )
        assert response.status_code == 400

    def test_create_restock_order_with_non_numeric_quantity(self, client, candidates):
        """Test that a non-numeric quantity is rejected."""
        response = client.post(
            "/api/restock-orders",
            json={"budget": 1000, "items": [{"sku": candidates[0]["sku"], "quantity": "many"}]},
        )
        assert response.status_code == 400

        data = response.json()
        assert "invalid quantity" in data["detail"].lower()

    def test_create_restock_order_missing_budget(self, client, candidates):
        """Test that a missing budget fails Pydantic validation."""
        response = client.post(
            "/api/restock-orders",
            json={"items": [{"sku": candidates[0]["sku"], "quantity": 1}]},
        )
        assert response.status_code == 422

    def test_rejected_order_is_not_stored(self, client):
        """Test that a rejected submission leaves no order behind."""
        client.post(
            "/api/restock-orders",
            json={"budget": 1000, "items": [{"sku": "NOT-A-SKU", "quantity": 5}]},
        )

        assert client.get("/api/restock-orders").json() == []
