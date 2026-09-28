from opendb.vectors.worker import MAX_BACKOFF_SECONDS
from opendb.vectors.worker import backoff


def test_backoff_keeps_poll_interval_while_healthy():
    assert backoff(5, 0) == 5
    assert backoff(5, -1) == 5


def test_backoff_grows_exponentially_and_caps():
    assert backoff(5, 1) == 10
    assert backoff(5, 2) == 20
    assert backoff(5, 3) == 40
    assert backoff(5, 20) == MAX_BACKOFF_SECONDS
