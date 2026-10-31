"""An antenna left in Overload is rebooted after a while and gets its gains and torque back."""

import logging
from types import SimpleNamespace

import pytest

import reachy_mini.daemon.backend.robot.backend as robot_backend
from reachy_mini.daemon.backend.robot.backend import (
    RobotBackend,
    dynamixel_reboot_packet,
)

OVERLOAD = {"right_antenna": ["Overload Error"]}


class FakeController:
    """The few ReachyMiniPyControlLoop calls the recovery makes, recorded."""

    def __init__(self) -> None:
        """Start with a motor that answers and has no error."""
        self.packets: list[bytes] = []
        self.answering = True
        self.error_byte = 0
        self.pids: list[tuple[int, int, int, int]] = []
        self.torque_on: list[list[int]] = []
        self.failing_torque_writes = 0

    def write_raw_packet(self, packet: bytes) -> list[int]:
        """Record a raw packet."""
        self.packets.append(bytes(packet))
        return []

    def async_read_raw_bytes(self, id: int, addr: int, length: int) -> list[int]:
        """Read the hardware error byte, or fail while the motor is booting."""
        assert addr == 70 and length == 1
        if not self.answering:
            raise RuntimeError("Communication error")
        return [self.error_byte]

    def async_write_pid_gains(self, id: int, p: int, i: int, d: int) -> None:
        """Record the gains written."""
        self.pids.append((id, p, i, d))

    def enable_torque_on_ids(self, ids: list[int]) -> None:
        """Record the motors given torque."""
        if self.failing_torque_writes:
            self.failing_torque_writes -= 1
            raise RuntimeError("Communication error")
        self.torque_on.append(list(ids))


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """Drive the backend's time.time() by hand."""
    now = [1000.0]
    monkeypatch.setattr(robot_backend, "time", SimpleNamespace(time=lambda: now[0]))
    return now


def make_backend(torque_enabled: bool = True) -> tuple[RobotBackend, FakeController]:
    """Build a backend with only what the recovery uses."""
    backend = object.__new__(RobotBackend)
    c = FakeController()
    backend.c = c  # type: ignore[assignment]
    backend.logger = logging.getLogger("test_overload_recovery")
    backend.name2id = {"right_antenna": 17, "left_antenna": 18}
    backend._motor_pids = {"right_antenna": (200, 0, 0), "left_antenna": (200, 0, 0)}
    backend._overload_since = {}
    backend._overload_reboots = {}
    backend._overload_gave_up = set()
    backend._rebooting = {}
    backend._reboot_grace_until = 0.0
    backend._torque_enabled = torque_enabled
    backend._partial_torque_override = False
    return backend, c


def test_reboot_packet_matches_the_protocol_example() -> None:
    """The packet is the e-Manual's, CRC included."""
    # Robotis e-Manual, Protocol 2.0, Reboot example for ID 1.
    assert dynamixel_reboot_packet(1) == bytes.fromhex("ff ff fd 00 01 03 00 08 2f 4e")


def test_overload_is_rebooted_after_a_while_and_torque_comes_back(
    clock: list[float],
) -> None:
    """Overload for 10 s: reboot, wait for the motor, restore its gains and torque."""
    backend, c = make_backend()

    backend._recover_overloads(OVERLOAD)
    clock[0] += 5
    backend._recover_overloads(OVERLOAD)
    assert c.packets == []

    clock[0] += 5
    backend._recover_overloads(OVERLOAD)
    assert c.packets == [dynamixel_reboot_packet(17)]
    assert backend._reboot_grace_until == clock[0] + RobotBackend.MOTOR_REBOOT_GRACE_S

    c.answering = False  # still booting
    clock[0] += 1
    backend._recover_overloads({})
    assert c.torque_on == []

    c.answering = True
    clock[0] += 1
    backend._recover_overloads({})
    assert c.pids == [(17, 200, 0, 0)]
    assert c.torque_on == [[17]]
    assert backend._rebooting == {}


def test_a_short_overload_is_left_alone(clock: list[float]) -> None:
    """An error that clears by itself is not counted toward the wait."""
    backend, c = make_backend()
    backend._recover_overloads(OVERLOAD)
    clock[0] += 3
    backend._recover_overloads({})
    clock[0] += 20
    backend._recover_overloads(OVERLOAD)
    assert c.packets == []


def test_torque_stays_off_while_the_motors_are_off(clock: list[float]) -> None:
    """With the motors off the reboot clears the error but leaves torque off."""
    backend, c = make_backend(torque_enabled=False)
    backend._recover_overloads(OVERLOAD)
    clock[0] += 10
    backend._recover_overloads(OVERLOAD)
    clock[0] += 1
    backend._recover_overloads({})
    assert c.pids == [(17, 200, 0, 0)]
    assert c.torque_on == []


def test_still_in_error_after_the_reboot_keeps_torque_off(clock: list[float]) -> None:
    """A motor still in error after its reboot does not get torque."""
    backend, c = make_backend()
    backend._recover_overloads(OVERLOAD)
    clock[0] += 10
    backend._recover_overloads(OVERLOAD)
    c.error_byte = 0x20
    clock[0] += 1
    backend._recover_overloads({})
    assert c.torque_on == []
    assert backend._rebooting == {}


def test_a_motor_that_does_not_come_back_is_given_up_after_the_grace(
    clock: list[float],
) -> None:
    """A motor silent past the grace is dropped; the 1 s loop rule applies again."""
    backend, c = make_backend()
    backend._recover_overloads(OVERLOAD)
    clock[0] += 10
    backend._recover_overloads(OVERLOAD)
    c.answering = False
    for _ in range(4):
        clock[0] += 1
        backend._recover_overloads({})
    assert backend._rebooting == {}
    assert c.torque_on == []
    assert clock[0] > backend._reboot_grace_until


def test_at_most_a_few_reboots_an_hour(clock: list[float]) -> None:
    """A blocked antenna gets a few reboots an hour, then is left off until later."""
    backend, c = make_backend()
    for _ in range(RobotBackend.OVERLOAD_RECOVER_MAX_PER_HOUR):
        backend._recover_overloads(OVERLOAD)
        clock[0] += 10
        backend._recover_overloads(OVERLOAD)
        clock[0] += 1
        backend._recover_overloads({})  # recovered, then blocked again
    backend._recover_overloads(OVERLOAD)
    clock[0] += 60
    backend._recover_overloads(OVERLOAD)
    assert len(c.packets) == RobotBackend.OVERLOAD_RECOVER_MAX_PER_HOUR
    assert "right_antenna" in backend._overload_gave_up

    clock[0] += 3600
    backend._recover_overloads(OVERLOAD)
    assert len(c.packets) == RobotBackend.OVERLOAD_RECOVER_MAX_PER_HOUR + 1
    assert "right_antenna" not in backend._overload_gave_up


def test_a_bus_error_while_restoring_is_retried(clock: list[float]) -> None:
    """A failed torque write keeps the motor pending; the next check finishes it."""
    backend, c = make_backend()
    backend._recover_overloads(OVERLOAD)
    clock[0] += 10
    backend._recover_overloads(OVERLOAD)
    c.failing_torque_writes = 1
    clock[0] += 1
    backend._recover_overloads({})
    assert "right_antenna" in backend._rebooting
    clock[0] += 1
    backend._recover_overloads({})
    assert c.torque_on == [[17]]
    assert backend._rebooting == {}
