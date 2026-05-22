from __future__ import annotations

from enum import Enum


class State(str, Enum):
    DISCONNECTED = "DISCONNECTED"
    IDLE = "IDLE"
    SCANNING = "SCANNING"
    LISTENING = "LISTENING"
    CHECKING_FREQUENCY = "CHECKING_FREQUENCY"
    CALLING_CQ = "CALLING_CQ"
    RECEIVING_REPLY = "RECEIVING_REPLY"
    HANDLING_PILEUP = "HANDLING_PILEUP"
    IN_QSO = "IN_QSO"
    CONFIRMING_LOG = "CONFIRMING_LOG"
    LOGGED = "LOGGED"
    STOPPED = "STOPPED"
    ERROR = "ERROR"

    def __str__(self) -> str:
        return self.value


class Event(str, Enum):
    """High-level FSM event names (distinct from bus event types)."""

    CONNECT = "connect"
    DISCONNECT = "disconnect"
    START_SESSION = "start_session"
    SCAN = "scan"
    CANDIDATE_PICKED = "candidate_picked"
    LISTEN_DONE_CLEAR = "listen_done_clear"
    LISTEN_DONE_BUSY = "listen_done_busy"
    CHECK_DONE_CLEAR = "check_done_clear"
    CHECK_DONE_BUSY = "check_done_busy"
    CQ_SENT = "cq_sent"
    CQ_MAX_REACHED = "cq_max_reached"
    REPLY_HEARD = "reply_heard"
    REPLY_TIMEOUT = "reply_timeout"
    PILEUP_SELECTED = "pileup_selected"
    QSO_COMPLETED = "qso_completed"
    LOG_CONFIRMED = "log_confirmed"
    LOG_FAILED = "log_failed"
    STOP = "stop"
    KILL = "kill"
    ERROR = "error"
    ACK = "ack"

    def __str__(self) -> str:
        return self.value
