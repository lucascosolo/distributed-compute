import unittest

from aipool.domain import ProviderErrorKind, ProviderProfile, ProviderState
from aipool.health import HealthManager
from aipool.storage import Store


class HealthTests(unittest.TestCase):
    def test_transient_failures_degrade_then_backoff(self) -> None:
        now = [100.0]
        store = Store()
        self.addCleanup(store.close)
        manager = HealthManager(store, clock=lambda: now[0], base_backoff=10, max_backoff=100)
        provider = ProviderProfile("p", "P", "fixture", state=ProviderState.HEALTHY)
        manager.profiles([provider])
        manager.failure(provider, ProviderErrorKind.INTERNAL, "bad response")
        self.assertEqual(store.health("p")["state"], ProviderState.DEGRADED.value)
        self.assertEqual(store.health("p")["next_probe_at"], 110.0)
        self.assertEqual(manager.profiles([provider])[0].state, ProviderState.DEGRADED)

    def test_rate_limit_is_excluded_until_probe_time(self) -> None:
        now = [100.0]
        store = Store()
        self.addCleanup(store.close)
        manager = HealthManager(store, clock=lambda: now[0], base_backoff=10)
        provider = ProviderProfile("p", "P", "fixture", state=ProviderState.HEALTHY)
        manager.profiles([provider])
        manager.failure(provider, ProviderErrorKind.RATE_LIMITED, "429")
        self.assertEqual(manager.profiles([provider])[0].state, ProviderState.RATE_LIMITED)
        now[0] = 110.0
        self.assertEqual(manager.profiles([provider])[0].state, ProviderState.DEGRADED)

    def test_auth_failure_stays_auth_required(self) -> None:
        store = Store()
        self.addCleanup(store.close)
        manager = HealthManager(store, clock=lambda: 100.0)
        provider = ProviderProfile("p", "P", "fixture", state=ProviderState.HEALTHY)
        manager.profiles([provider])
        manager.failure(provider, ProviderErrorKind.AUTH, "missing key")
        self.assertEqual(manager.profiles([provider])[0].state, ProviderState.AUTH_REQUIRED)


if __name__ == "__main__":
    unittest.main()
