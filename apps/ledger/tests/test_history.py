from decimal import Decimal

from rest_framework.test import APITestCase

from apps.ledger import services
from apps.ledger.models import Account
from apps.ledger.tests.helpers import ErrorAssertions, at_minute


class HistoryTestCase(ErrorAssertions, APITestCase):
    def setUp(self):
        self.account = Account.objects.create(name="Priya Patel")
        self.url = f"/api/accounts/{self.account.id}/transactions"

    def add(self, minute, type="credit", amount="10.00", reference_id=None, account=None):
        with at_minute(minute):
            return services.post_transaction(
                (account or self.account).id, type, Decimal(amount), reference_id or f"REF-{minute:03d}"
            )

    def add_many(self, count):
        for minute in range(1, count + 1):
            self.add(minute)

    def get(self, **params):
        return self.client.get(self.url, params)

    def refs(self, response):
        return [txn["reference_id"] for txn in response.json()["data"]]


class HistoryTests(HistoryTestCase):
    def test_empty_history(self):
        response = self.get()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {"data": [], "pagination": {"page": 1, "limit": 10, "total_records": 0, "total_pages": 0}},
        )

    def test_returns_transactions_newest_first(self):
        self.add_many(3)
        response = self.get()

        self.assertEqual(self.refs(response), ["REF-003", "REF-002", "REF-001"])
        self.assertEqual(
            set(response.json()["data"][0]),
            {"id", "account_id", "type", "amount", "description", "reference_id", "transfer_id", "created_at"},
        )

    def test_same_timestamp_is_ordered_by_id(self):
        txns = [self.add(5, reference_id=f"SAME-{n}") for n in range(4)]
        expected = [txn.reference_id for txn in sorted(txns, key=lambda txn: txn.id, reverse=True)]
        self.assertEqual(self.refs(self.get()), expected)

    def test_only_this_accounts_transactions_are_returned(self):
        other = Account.objects.create(name="Rohan Mehta")
        self.add(1)
        self.add(2, account=other)
        self.assertEqual(self.refs(self.get()), ["REF-001"])


class HistoryFilterTests(HistoryTestCase):
    def setUp(self):
        super().setUp()
        self.add(1, "credit", "1000.00")
        self.add(2, "debit", "100.00")
        self.add(3, "credit", "50.00")
        self.add(4, "debit", "20.00")

    def test_without_type_returns_both(self):
        self.assertEqual(self.refs(self.get()), ["REF-004", "REF-003", "REF-002", "REF-001"])

    def test_filter_credits(self):
        response = self.get(type="credit")
        self.assertEqual(self.refs(response), ["REF-003", "REF-001"])
        self.assertEqual(response.json()["pagination"]["total_records"], 2)

    def test_filter_debits(self):
        self.assertEqual(self.refs(self.get(type="debit")), ["REF-004", "REF-002"])

    def test_filter_combined_with_pagination(self):
        response = self.get(type="debit", page=2, limit=1)
        self.assertEqual(self.refs(response), ["REF-002"])
        self.assertEqual(
            response.json()["pagination"], {"page": 2, "limit": 1, "total_records": 2, "total_pages": 2}
        )


class HistoryPaginationTests(HistoryTestCase):
    def test_defaults_to_first_page_of_ten(self):
        self.add_many(12)
        response = self.get()
        self.assertEqual(len(response.json()["data"]), 10)
        self.assertEqual(
            response.json()["pagination"], {"page": 1, "limit": 10, "total_records": 12, "total_pages": 2}
        )

    def test_pages_cover_every_record_once(self):
        self.add_many(25)
        pages = [self.refs(self.get(page=page, limit=10)) for page in (1, 2, 3)]

        self.assertEqual([len(page) for page in pages], [10, 10, 5])
        self.assertEqual(sum(pages, []), [f"REF-{minute:03d}" for minute in range(25, 0, -1)])

    def test_page_past_the_end_is_empty(self):
        self.add_many(5)
        response = self.get(page=3, limit=5)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {"data": [], "pagination": {"page": 3, "limit": 5, "total_records": 5, "total_pages": 1}},
        )

    def test_empty_params_fall_back_to_defaults(self):
        response = self.client.get(f"{self.url}?page=&limit=&type=")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["pagination"]["limit"], 10)


class HistoryValidationTests(HistoryTestCase):
    def test_invalid_query_params(self):
        cases = [
            ({"page": 0}, "page"),
            ({"page": -1}, "page"),
            ({"page": "abc"}, "page"),
            ({"page": "1.5"}, "page"),
            ({"limit": 0}, "limit"),
            ({"limit": 101}, "limit"),
            ({"limit": "ten"}, "limit"),
            ({"type": "refund"}, "type"),
            ({"type": "CREDIT"}, "type"),
        ]
        for params, field in cases:
            with self.subTest(params=params):
                error = self.assert_error(self.get(**params), 400, "VALIDATION_ERROR")
                self.assertIn(field, error["details"])

    def test_unknown_account_is_not_found(self):
        response = self.client.get("/api/accounts/00000000-0000-4000-8000-000000000999/transactions")
        self.assert_error(response, 404, "ACCOUNT_NOT_FOUND")

    def test_malformed_account_id_is_rejected(self):
        self.assert_error(self.client.get("/api/accounts/abc/transactions"), 400, "INVALID_ACCOUNT_ID")

    def test_malformed_account_id_is_checked_before_query(self):
        self.assert_error(self.client.get("/api/accounts/abc/transactions?page=0"), 400, "INVALID_ACCOUNT_ID")

    def test_query_is_validated_before_account_lookup(self):
        url = "/api/accounts/00000000-0000-4000-8000-000000000999/transactions?page=0"
        self.assert_error(self.client.get(url), 400, "VALIDATION_ERROR")
