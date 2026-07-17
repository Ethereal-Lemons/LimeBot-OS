import unittest

from core.provider_circuit_breaker import ProviderCircuitBreaker


class TestProviderCircuitBreaker(unittest.TestCase):
    def test_transient_failures_recover_with_single_half_open_probe(self):
        breaker = ProviderCircuitBreaker(
            failure_threshold=2,
            failure_window_s=60,
            recovery_timeout_s=10,
        )
        key = "gemini|gemini-2.0-flash||gemini"
        credential = ProviderCircuitBreaker.credential_fingerprint("key-a")

        self.assertTrue(breaker.allow(key, credential_fingerprint=credential, now=0))
        breaker.record_failure(key, credential_fingerprint=credential, now=0)
        breaker.record_failure(key, credential_fingerprint=credential, now=1)
        self.assertFalse(breaker.allow(key, credential_fingerprint=credential, now=2))

        self.assertTrue(breaker.allow(key, credential_fingerprint=credential, now=11))
        self.assertFalse(breaker.allow(key, credential_fingerprint=credential, now=11))
        breaker.record_success(key, credential_fingerprint=credential)
        self.assertTrue(breaker.allow(key, credential_fingerprint=credential, now=12))

    def test_authentication_failure_stays_open_until_credential_changes(self):
        breaker = ProviderCircuitBreaker(recovery_timeout_s=1)
        key = "xai|grok-4||openai"
        old_credential = ProviderCircuitBreaker.credential_fingerprint("old")
        new_credential = ProviderCircuitBreaker.credential_fingerprint("new")

        self.assertTrue(
            breaker.allow(key, credential_fingerprint=old_credential, now=0)
        )
        breaker.record_failure(
            key,
            credential_fingerprint=old_credential,
            authentication=True,
            now=0,
        )
        self.assertFalse(
            breaker.allow(key, credential_fingerprint=old_credential, now=100)
        )
        self.assertTrue(
            breaker.allow(key, credential_fingerprint=new_credential, now=100)
        )

    def test_cancelled_half_open_probe_is_recoverable(self):
        breaker = ProviderCircuitBreaker(
            failure_threshold=1,
            recovery_timeout_s=10,
        )
        key = "openai|gpt-5||openai"
        credential = ProviderCircuitBreaker.credential_fingerprint("key")
        breaker.record_failure(key, credential_fingerprint=credential, now=0)
        self.assertTrue(breaker.allow(key, credential_fingerprint=credential, now=11))
        breaker.record_aborted(key, credential_fingerprint=credential, now=11)
        self.assertFalse(breaker.allow(key, credential_fingerprint=credential, now=12))
        self.assertTrue(breaker.allow(key, credential_fingerprint=credential, now=22))


if __name__ == "__main__":
    unittest.main()
