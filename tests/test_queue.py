"""ChatQueue tests: FIFO per chat, parallelism, stop, and the active-chat cap."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Iterator
from concurrent.futures import CancelledError

import pytest

from sanka.models import InboundMessage
from sanka.policy import Role
from sanka.queue import AgentJob, ChatQueue, QueueBusyError, StopOutcome

ROLE = Role.COMMITTEE


def message(
    chat_id: str,
    text: str,
    sender_id: str = "sender-a",
) -> InboundMessage:
    return InboundMessage(chat_id=chat_id, sender_id=sender_id, text=text)


def wait_until(predicate: Callable[[], bool], timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while not predicate():
        assert time.monotonic() < deadline, "condition not met before timeout"
        time.sleep(0.005)


@pytest.fixture
def make_queue() -> Iterator[Callable[..., ChatQueue[str]]]:
    created: list[ChatQueue[str]] = []

    def factory(runner: Callable[[AgentJob[str]], str], **kwargs: int) -> ChatQueue[str]:
        queue: ChatQueue[str] = ChatQueue(runner, **kwargs)
        created.append(queue)
        return queue

    yield factory

    for queue in created:
        queue.shutdown(wait=True)


def test_same_chat_runs_in_fifo_order(
    make_queue: Callable[..., ChatQueue[str]],
) -> None:
    started = threading.Event()
    release = threading.Event()
    order: list[str] = []

    def runner(job: AgentJob[str]) -> str:
        order.append(job.message.text)
        started.set()
        assert release.wait(5)
        return job.message.text

    queue = make_queue(runner)

    first = queue.submit(message("chat-1", "pertama"), ROLE)
    assert started.wait(5)
    second = queue.submit(message("chat-1", "kedua"), ROLE)

    assert not second.done()
    release.set()

    assert first.result(5) == "pertama"
    assert second.result(5) == "kedua"
    assert order == ["pertama", "kedua"]


def test_different_chats_run_in_parallel(
    make_queue: Callable[..., ChatQueue[str]],
) -> None:
    barrier = threading.Barrier(2)

    def runner(job: AgentJob[str]) -> str:
        barrier.wait(timeout=5)
        return job.chat_id

    queue = make_queue(runner)
    first = queue.submit(message("chat-a", "halo"), ROLE)
    second = queue.submit(message("chat-b", "halo"), ROLE)

    assert first.result(5) == "chat-a"
    assert second.result(5) == "chat-b"


def test_stop_by_sender_cancels_current_job(
    make_queue: Callable[..., ChatQueue[str]],
) -> None:
    started = threading.Event()

    def runner(job: AgentJob[str]) -> str:
        started.set()
        job.cancel_event.wait(5)
        return "hasil terlambat"

    queue = make_queue(runner)
    future = queue.submit(message("chat-1", "halo"), ROLE)
    assert started.wait(5)

    assert queue.stop("chat-1", "sender-a", False) is StopOutcome.STOPPED

    with pytest.raises(CancelledError):
        future.result(5)


def test_stop_by_owner_cancels_job_of_another_sender(
    make_queue: Callable[..., ChatQueue[str]],
) -> None:
    started = threading.Event()

    def runner(job: AgentJob[str]) -> str:
        started.set()
        job.cancel_event.wait(5)
        return "hasil terlambat"

    queue = make_queue(runner)
    future = queue.submit(message("chat-1", "halo", sender_id="lain"), ROLE)
    assert started.wait(5)

    assert queue.stop("chat-1", "owner", True) is StopOutcome.STOPPED

    with pytest.raises(CancelledError):
        future.result(5)


def test_stop_by_other_sender_is_forbidden(
    make_queue: Callable[..., ChatQueue[str]],
) -> None:
    started = threading.Event()
    release = threading.Event()

    def runner(job: AgentJob[str]) -> str:
        started.set()
        assert release.wait(5)
        return "selesai"

    queue = make_queue(runner)
    future = queue.submit(message("chat-1", "halo", sender_id="sender-a"), ROLE)
    assert started.wait(5)

    assert queue.stop("chat-1", "sender-b", False) is StopOutcome.FORBIDDEN
    assert not future.cancelled()

    release.set()
    assert future.result(5) == "selesai"


def test_stop_cancels_queued_futures(
    make_queue: Callable[..., ChatQueue[str]],
) -> None:
    started = threading.Event()
    release = threading.Event()

    def runner(job: AgentJob[str]) -> str:
        started.set()
        assert release.wait(5)
        return job.message.text

    queue = make_queue(runner)
    current = queue.submit(message("chat-1", "satu"), ROLE)
    assert started.wait(5)

    queued_one = queue.submit(message("chat-1", "dua"), ROLE)
    queued_two = queue.submit(message("chat-1", "tiga"), ROLE)

    assert queue.stop("chat-1", "sender-a", False) is StopOutcome.STOPPED
    assert queued_one.cancelled()
    assert queued_two.cancelled()

    release.set()
    with pytest.raises(CancelledError):
        current.result(5)


def test_stop_on_idle_chat_returns_idle(
    make_queue: Callable[..., ChatQueue[str]],
) -> None:
    queue = make_queue(lambda job: "selesai")

    assert queue.stop("unknown", "sender-a", False) is StopOutcome.IDLE

    future = queue.submit(message("chat-1", "halo"), ROLE)
    assert future.result(5) == "selesai"
    wait_until(lambda: not queue._queues)

    assert queue.stop("chat-1", "sender-a", False) is StopOutcome.IDLE


def test_active_chat_cap_and_reuse_after_worker_exits(
    make_queue: Callable[..., ChatQueue[str]],
) -> None:
    started = threading.Event()
    release = threading.Event()

    def runner(job: AgentJob[str]) -> str:
        started.set()
        assert release.wait(5)
        return job.message.text

    queue = make_queue(runner, max_active_chats=1)

    first = queue.submit(message("chat-a", "satu"), ROLE)
    assert started.wait(5)

    with pytest.raises(QueueBusyError):
        queue.submit(message("chat-b", "ditolak"), ROLE)

    # The cap counts chats, not jobs: a second job in a live chat is accepted.
    second = queue.submit(message("chat-a", "dua"), ROLE)

    release.set()
    assert first.result(5) == "satu"
    assert second.result(5) == "dua"
    wait_until(lambda: not queue._queues)

    third = queue.submit(message("chat-b", "tiga"), ROLE)
    assert third.result(5) == "tiga"


def test_shutdown_cancels_pending_and_current_jobs(
    make_queue: Callable[..., ChatQueue[str]],
) -> None:
    started = threading.Event()

    def runner(job: AgentJob[str]) -> str:
        started.set()
        job.cancel_event.wait(5)
        return "diabaikan"

    queue = make_queue(runner)
    current = queue.submit(message("chat-1", "satu"), ROLE)
    assert started.wait(5)
    pending = queue.submit(message("chat-1", "dua"), ROLE)

    queue.shutdown(wait=True)

    with pytest.raises(CancelledError):
        current.result(5)
    assert pending.cancelled()

    with pytest.raises(RuntimeError):
        queue.submit(message("chat-1", "tiga"), ROLE)
