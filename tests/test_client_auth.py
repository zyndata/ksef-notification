"""Authentication, refresh and their fallbacks (docs/ARCHITECTURE.md § Token lifecycle).

Every test runs the real client against the scripted fake in `ksef_fake.py`; a protected
call (an empty metadata query) is the trigger, as it is in the integration.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import timedelta

import pytest

from custom_components.ksef_notification.client import (
    AuthErrorReason,
    KsefAuthError,
    KsefClient,
    KsefMalformedResponseError,
    KsefRateLimitError,
    KsefTemporaryError,
    RateLimitGroup,
)

from .ksef_fake import (
    AUTH_ROUTES,
    KEY_ID,
    KSEF_TOKEN,
    NIP,
    START,
    FakeClock,
    FakeKsef,
    Reply,
    decrypt_token,
    error_reply,
    fixture,
    key_entry,
    status_reply,
)

SINCE = START - timedelta(hours=1)


async def query(client: KsefClient) -> None:
    await client.query_metadata(SINCE)


# --- the full sequence ------------------------------------------------------------------------


async def test_full_sequence_in_order(client: KsefClient, ksef: FakeKsef) -> None:
    await query(client)

    assert ksef.routes() == [*AUTH_ROUTES, "metadata"]


async def test_token_is_encrypted_with_oaep_sha256_and_the_challenge_timestamp(
    client: KsefClient, ksef: FakeKsef, clock: FakeClock
) -> None:
    await query(client)

    init = ksef.last("init").body
    timestamp_ms = int(START.timestamp() * 1000)
    assert decrypt_token(init["encryptedToken"]) == f"{KSEF_TOKEN}|{timestamp_ms}"
    assert init["challenge"] == fixture("auth_challenge.json")["challenge"]
    assert init["contextIdentifier"] == {"type": "Nip", "value": NIP}
    assert init["publicKeyId"] == KEY_ID


async def test_bearers_operation_token_for_status_and_redeem_access_token_after(
    client: KsefClient, ksef: FakeKsef
) -> None:
    await query(client)

    operation = fixture("auth_init.json")["authenticationToken"]["token"]
    assert ksef.last("status").bearer == operation
    assert ksef.last("redeem").bearer == operation
    assert ksef.last("status").path == "/auth/" + fixture("auth_init.json")["referenceNumber"]
    assert ksef.last("metadata").bearer == "synthetic-access-token-1"
    assert ksef.last("challenge").bearer is None
    assert all(call.headers["X-Error-Format"] == "problem-details" for call in ksef.calls)


async def test_access_token_is_reused_while_valid(
    client: KsefClient, ksef: FakeKsef, clock: FakeClock
) -> None:
    await query(client)
    clock.advance(timedelta(minutes=13))  # 2 min left, above the 60 s margin
    await query(client)

    assert ksef.count("redeem") == 1
    assert ksef.count("refresh") == 0
    assert ksef.last("metadata").bearer == "synthetic-access-token-1"


async def test_concurrent_calls_authenticate_once(client: KsefClient, ksef: FakeKsef) -> None:
    await asyncio.gather(query(client), query(client), query(client))

    assert ksef.count("redeem") == 1
    assert ksef.count("metadata") == 3


# --- the public key ---------------------------------------------------------------------------


async def test_key_choice_valid_now_and_newest(client: KsefClient, ksef: FakeKsef) -> None:
    ksef.script(
        "keys",
        Reply(
            json=[
                key_entry(key_id="expired", valid_to=START - timedelta(seconds=1)),
                key_entry(key_id="future", valid_from=START + timedelta(days=1)),
                key_entry(key_id="other-usage", usage=("SymmetricKeyEncryption",)),
                key_entry(key_id="older", valid_from=START - timedelta(days=60)),
                key_entry(key_id="newest", valid_from=START - timedelta(days=1)),
            ]
        ),
    )

    await query(client)

    assert ksef.last("init").body["publicKeyId"] == "newest"


async def test_no_usable_key_is_malformed(client: KsefClient, ksef: FakeKsef) -> None:
    ksef.script("keys", Reply(json=[key_entry(usage=("SymmetricKeyEncryption",))]))

    with pytest.raises(KsefMalformedResponseError):
        await query(client)
    assert "challenge" not in ksef.routes()


async def test_key_list_cached_until_the_certificate_expires(
    client: KsefClient, ksef: FakeKsef, clock: FakeClock
) -> None:
    ksef.script("keys", Reply(json=[key_entry(valid_to=START + timedelta(days=10))]))
    ksef.script("refresh", error_reply(21301))

    await query(client)
    clock.advance(timedelta(days=8))  # refresh token expired → full authentication
    await query(client)
    assert ksef.count("keys") == 1

    clock.advance(timedelta(days=3))  # certificate expired → list downloaded again
    ksef.script(
        "keys",
        Reply(json=[key_entry(key_id="rotated", valid_to=START + timedelta(days=60))]),
    )
    await query(client)
    assert ksef.count("keys") == 2
    assert ksef.last("init").body["publicKeyId"] == "rotated"


async def test_unknown_key_reloads_the_list_and_retries_once(
    client: KsefClient, ksef: FakeKsef
) -> None:
    ksef.script("init", error_reply(21470), Reply(status=202, json=fixture("auth_init.json")))
    ksef.script("keys", Reply(json=[key_entry(key_id="old")]), Reply(json=[key_entry()]))

    await query(client)

    assert ksef.count("keys") == 2
    assert [call.body["publicKeyId"] for call in ksef.calls if call.route == "init"] == [
        "old",
        KEY_ID,
    ]


async def test_unknown_key_twice_is_temporary(client: KsefClient, ksef: FakeKsef) -> None:
    ksef.script("init", error_reply(21470))

    with pytest.raises(KsefTemporaryError):
        await query(client)
    assert ksef.count("init") == 2


# --- the status poll --------------------------------------------------------------------------


async def test_status_polled_with_backoff_until_success(
    client: KsefClient, ksef: FakeKsef, clock: FakeClock
) -> None:
    ksef.script("status", status_reply(100), status_reply(100), status_reply(200))

    await query(client)

    assert ksef.count("status") == 3
    assert clock.sleeps == [0.5, 1.0, 2.0]


async def test_status_still_in_progress_after_the_timeout_is_temporary(
    client: KsefClient, ksef: FakeKsef, clock: FakeClock
) -> None:
    ksef.script("status", status_reply(100))

    with pytest.raises(KsefTemporaryError):
        await query(client)

    assert sum(clock.sleeps) <= 30 + 4
    assert ksef.count("status") == len(clock.sleeps) <= 12
    assert "redeem" not in ksef.routes()


@pytest.mark.parametrize(
    ("status", "reason"),
    [
        (status_reply(450, "Token unieważniony"), AuthErrorReason.TOKEN_INVALID),
        (status_reply(450, "Token nieaktywny"), AuthErrorReason.TOKEN_INVALID),
        (status_reply(450, "Nieprawidłowe szyfrowanie tokena"), AuthErrorReason.TOKEN_INVALID),
        (status_reply(450), AuthErrorReason.TOKEN_INVALID),
        (Reply(json=fixture("auth_status_token_revoked.json")), AuthErrorReason.TOKEN_INVALID),
        (status_reply(415, "Brak przypisanych uprawnień"), AuthErrorReason.NO_PERMISSION),
        (status_reply(470), AuthErrorReason.BLOCKED),
        (status_reply(480, "Podejrzenie incydentu bezpieczeństwa."), AuthErrorReason.BLOCKED),
    ],
)
async def test_status_needing_the_user(
    client: KsefClient, ksef: FakeKsef, status: Reply, reason: AuthErrorReason
) -> None:
    ksef.script("status", status)

    with pytest.raises(KsefAuthError) as caught:
        await query(client)

    assert caught.value.reason is reason
    assert ksef.count("challenge") == 1  # not retried
    assert "redeem" not in ksef.routes()


@pytest.mark.parametrize("code", [425, 460, 500, 550, 999])
async def test_other_failed_statuses_are_temporary(
    client: KsefClient, ksef: FakeKsef, code: int
) -> None:
    ksef.script("status", status_reply(code))

    with pytest.raises(KsefTemporaryError):
        await query(client)
    assert ksef.count("challenge") == 1


@pytest.mark.parametrize(
    "detail", ["Nieprawidłowe wyzwanie autoryzacyjne", "Nieprawidłowy czas tokena"]
)
async def test_challenge_problems_are_retried_once_with_a_new_challenge(
    client: KsefClient, ksef: FakeKsef, detail: str
) -> None:
    ksef.script("status", status_reply(450, detail), status_reply(200))

    await query(client)

    assert ksef.count("challenge") == 2
    assert ksef.count("redeem") == 1


async def test_challenge_problem_twice_is_temporary(client: KsefClient, ksef: FakeKsef) -> None:
    ksef.script("status", status_reply(450, "Nieprawidłowe wyzwanie autoryzacyjne"))

    with pytest.raises(KsefTemporaryError):
        await query(client)
    assert ksef.count("challenge") == 2


async def test_invalid_challenge_at_init_is_retried(client: KsefClient, ksef: FakeKsef) -> None:
    ksef.script("init", error_reply(21111), Reply(status=202, json=fixture("auth_init.json")))

    await query(client)

    assert ksef.count("challenge") == 2


async def test_validation_error_at_init_means_a_bad_token(
    client: KsefClient, ksef: FakeKsef
) -> None:
    ksef.script("init", error_reply(21405, "synthetic validation message"))

    with pytest.raises(KsefAuthError) as caught:
        await query(client)
    assert caught.value.reason is AuthErrorReason.TOKEN_INVALID


# --- redeem -----------------------------------------------------------------------------------


@pytest.mark.parametrize("legacy", [False, True])
async def test_redeem_with_revoked_ksef_token(
    client: KsefClient, ksef: FakeKsef, legacy: bool
) -> None:
    ksef.script("redeem", error_reply(21301, "Token KSeF został unieważniony.", legacy=legacy))

    with pytest.raises(KsefAuthError) as caught:
        await query(client)
    assert caught.value.reason is AuthErrorReason.TOKEN_INVALID


async def test_redeem_for_a_deceased_person_is_blocked(client: KsefClient, ksef: FakeKsef) -> None:
    ksef.script("redeem", error_reply(21308))

    with pytest.raises(KsefAuthError) as caught:
        await query(client)
    assert caught.value.reason is AuthErrorReason.BLOCKED


async def test_redeem_already_redeemed_is_temporary(client: KsefClient, ksef: FakeKsef) -> None:
    ksef.script("redeem", error_reply(21301, "Tokeny zostały już pobrane."))

    with pytest.raises(KsefTemporaryError):
        await query(client)


async def test_redeem_without_tokens_is_malformed(client: KsefClient, ksef: FakeKsef) -> None:
    ksef.script("redeem", Reply(json={"accessToken": {"token": "x"}}))

    with pytest.raises(KsefMalformedResponseError):
        await query(client)


# --- refresh ----------------------------------------------------------------------------------


async def test_expiring_access_token_is_refreshed_with_the_refresh_token(
    client: KsefClient, ksef: FakeKsef, clock: FakeClock
) -> None:
    await query(client)
    clock.advance(timedelta(minutes=14, seconds=30))  # 30 s left: below the margin
    await query(client)

    refresh = ksef.last("refresh")
    assert refresh.bearer == "synthetic-refresh-token-1"
    assert ksef.last("metadata").bearer == "synthetic-access-token-r1"
    assert ksef.count("redeem") == 1


async def test_refresh_token_is_not_rotated(
    client: KsefClient, ksef: FakeKsef, clock: FakeClock
) -> None:
    await query(client)
    for _ in range(3):
        clock.advance(timedelta(minutes=15))
        await query(client)

    assert ksef.count("refresh") == 3
    assert {call.bearer for call in ksef.calls if call.route == "refresh"} == {
        "synthetic-refresh-token-1"
    }
    assert ksef.count("redeem") == 1


@pytest.mark.parametrize(
    "refused",
    [
        error_reply(21301, "Status uwierzytelniania (425) nie pozwala na odświeżenie"),
        error_reply(21301, "Token KSeF został unieważniony."),
        error_reply(21304),
        Reply(status=401),
        Reply(status=403),
    ],
)
async def test_refused_refresh_falls_back_to_full_authentication(
    client: KsefClient, ksef: FakeKsef, clock: FakeClock, refused: Reply
) -> None:
    await query(client)
    ksef.script("refresh", refused)
    clock.advance(timedelta(minutes=15))

    await query(client)

    assert ksef.count("refresh") == 1
    assert ksef.count("redeem") == 2
    assert ksef.last("metadata").bearer == "synthetic-access-token-2"


async def test_revoked_ksef_token_surfaces_through_the_fallback(
    client: KsefClient, ksef: FakeKsef, clock: FakeClock
) -> None:
    await query(client)
    ksef.script("refresh", error_reply(21301, "Token KSeF został unieważniony."))
    ksef.script("status", status_reply(450, "Token unieważniony"))
    clock.advance(timedelta(minutes=15))

    with pytest.raises(KsefAuthError) as caught:
        await query(client)
    assert caught.value.reason is AuthErrorReason.TOKEN_INVALID


async def test_expired_refresh_token_goes_straight_to_full_authentication(
    client: KsefClient, ksef: FakeKsef, clock: FakeClock
) -> None:
    await query(client)
    clock.advance(timedelta(days=7))

    await query(client)

    assert ksef.count("refresh") == 0
    assert ksef.count("redeem") == 2


async def test_refresh_for_a_deceased_person_is_blocked(
    client: KsefClient, ksef: FakeKsef, clock: FakeClock
) -> None:
    await query(client)
    ksef.script("refresh", error_reply(21308))
    clock.advance(timedelta(minutes=15))

    with pytest.raises(KsefAuthError) as caught:
        await query(client)
    assert caught.value.reason is AuthErrorReason.BLOCKED


async def test_refresh_outage_is_temporary(
    client: KsefClient, ksef: FakeKsef, clock: FakeClock
) -> None:
    await query(client)
    ksef.script("refresh", Reply(status=503))
    clock.advance(timedelta(minutes=15))

    with pytest.raises(KsefTemporaryError):
        await query(client)
    assert ksef.count("redeem") == 1


async def test_auth_rate_limit(client: KsefClient, ksef: FakeKsef) -> None:
    ksef.script("challenge", Reply(status=429, headers={"Retry-After": "7"}))

    with pytest.raises(KsefRateLimitError) as caught:
        await query(client)
    assert caught.value.group is RateLimitGroup.AUTH
    assert caught.value.retry_after_s == 7


# --- a protected call refused -----------------------------------------------------------------


async def test_401_gets_a_new_token_and_repeats_once(client: KsefClient, ksef: FakeKsef) -> None:
    ksef.script("metadata", Reply(status=401), Reply(json=fixture("metadata_page.json")))

    result = await client.query_metadata(SINCE)

    assert len(result.invoices) == 2
    assert ksef.count("metadata") == 2
    assert ksef.count("refresh") == 1  # the refresh token was still good
    bearers = [call.bearer for call in ksef.calls if call.route == "metadata"]
    assert bearers == ["synthetic-access-token-1", "synthetic-access-token-r1"]


async def test_401_twice_is_temporary(client: KsefClient, ksef: FakeKsef) -> None:
    ksef.script("metadata", Reply(status=401))

    with pytest.raises(KsefTemporaryError):
        await query(client)
    assert ksef.count("metadata") == 2


async def test_403_missing_permission(client: KsefClient, ksef: FakeKsef) -> None:
    ksef.script("metadata", Reply(status=403, json=fixture("error_403_missing_permissions.json")))

    with pytest.raises(KsefAuthError) as caught:
        await query(client)
    assert caught.value.reason is AuthErrorReason.NO_PERMISSION


async def test_403_security_block(client: KsefClient, ksef: FakeKsef) -> None:
    body = fixture("error_403_missing_permissions.json")
    body["reasonCode"] = "security-service-blocked"
    ksef.script("metadata", Reply(status=403, json=body))

    with pytest.raises(KsefAuthError) as caught:
        await query(client)
    assert caught.value.reason is AuthErrorReason.BLOCKED


# --- closing the session ----------------------------------------------------------------------


async def test_close_revokes_the_session_and_forgets_the_tokens(
    client: KsefClient, ksef: FakeKsef
) -> None:
    await query(client)
    await client.async_close()

    assert ksef.last("revoke").bearer == "synthetic-access-token-1"
    await query(client)
    assert ksef.count("redeem") == 2


async def test_close_without_a_session_sends_nothing(client: KsefClient, ksef: FakeKsef) -> None:
    await client.async_close()

    assert ksef.calls == []


async def test_close_refreshes_an_expired_access_token_first(
    client: KsefClient, ksef: FakeKsef, clock: FakeClock
) -> None:
    await query(client)
    clock.advance(timedelta(minutes=20))

    await client.async_close()

    assert ksef.routes()[-2:] == ["refresh", "revoke"]
    assert ksef.last("revoke").bearer == "synthetic-access-token-r1"


async def test_close_never_authenticates_just_to_revoke(
    client: KsefClient, ksef: FakeKsef, clock: FakeClock
) -> None:
    await query(client)
    clock.advance(timedelta(days=8))
    calls = len(ksef.calls)

    await client.async_close()

    assert len(ksef.calls) == calls


@pytest.mark.parametrize(
    "failure", [Reply(status=500), Reply(exc=TimeoutError()), Reply(status=401)]
)
async def test_close_swallows_failures(client: KsefClient, ksef: FakeKsef, failure: Reply) -> None:
    await query(client)
    ksef.script("revoke", failure)

    await client.async_close()  # no exception

    await query(client)
    assert ksef.count("redeem") == 2  # tokens dropped anyway


# --- secrets ----------------------------------------------------------------------------------


async def test_no_token_reaches_a_log_line_or_an_error(
    client: KsefClient, ksef: FakeKsef, clock: FakeClock, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    errors: list[str] = []
    await query(client)
    clock.advance(timedelta(minutes=15))
    ksef.script("refresh", Reply(status=401))
    ksef.script("status", status_reply(450, f"Token nie może być użyty w kontekście {NIP}"))
    try:
        await query(client)
    except KsefAuthError as err:
        errors.append(repr(err))
    ksef.script("status", status_reply(200))
    ksef.script("metadata", Reply(status=401))
    try:
        await query(client)
    except KsefTemporaryError as err:
        errors.append(repr(err))

    text = caplog.text + "".join(errors) + repr(client.__dict__)
    assert len(errors) == 2
    for secret in (KSEF_TOKEN, "synthetic-access-token", "synthetic-refresh-token", NIP):
        assert secret not in text
