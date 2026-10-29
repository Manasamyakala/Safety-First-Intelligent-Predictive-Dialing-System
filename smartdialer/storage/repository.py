"""Database repository with atomic transactions, optimistic locking, and lease management."""

from __future__ import annotations
import json
import sqlite3
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

from smartdialer.models.agent import Agent, AgentState
from smartdialer.models.call import Call, CallState, TERMINAL_CALL_STATES, CALL_STATE_RANK
from smartdialer.models.borrower import Borrower, BorrowerState
from smartdialer.models.campaign import Campaign, DialingMode
from smartdialer.storage.schema import SQL_SCHEMA


class Repository:
    """Thread-safe SQLite repository supporting concurrent workers with WAL mode and ACID transactions."""

    def __init__(self, db_path: str = ":memory:"):
        self.db_path = db_path
        self._local = threading.local()
        self._lock = threading.Lock()
        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        if not hasattr(self._local, "conn") or self._local.conn is None:
            conn = sqlite3.connect(
                self.db_path,
                timeout=10.0,
                check_same_thread=False,
                isolation_level=None,  # Autocommit mode, we manage transactions explicitly
            )
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.execute("PRAGMA busy_timeout=10000;")
            conn.execute("PRAGMA foreign_keys=ON;")
            self._local.conn = conn
        return self._local.conn

    def _init_db(self) -> None:
        conn = self._get_connection()
        with self._lock:
            conn.executescript(SQL_SCHEMA)

    def close(self) -> None:
        if hasattr(self._local, "conn") and self._local.conn is not None:
            self._local.conn.close()
            self._local.conn = None

    # ==========================================
    # AGENT OPERATIONS & ATOMIC CONCURRENCY
    # ==========================================

    def create_agent(self, agent: Agent) -> None:
        conn = self._get_connection()
        conn.execute(
            """
            INSERT INTO agents (id, name, status, reserved_by_worker, reserved_at, current_call_id, version, last_state_change, skills)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                agent.id,
                agent.name,
                agent.status.value,
                agent.reserved_by_worker,
                agent.reserved_at,
                agent.current_call_id,
                agent.version,
                agent.last_state_change,
                json.dumps(agent.skills),
            ),
        )

    def get_agent(self, agent_id: str) -> Optional[Agent]:
        conn = self._get_connection()
        row = conn.execute("SELECT * FROM agents WHERE id = ?", (agent_id,)).fetchone()
        if not row:
            return None
        return Agent(
            id=row["id"],
            name=row["name"],
            status=AgentState(row["status"]),
            reserved_by_worker=row["reserved_by_worker"],
            reserved_at=row["reserved_at"],
            current_call_id=row["current_call_id"],
            version=row["version"],
            last_state_change=row["last_state_change"],
            skills=json.loads(row["skills"]),
        )

    def get_all_agents(self) -> List[Agent]:
        conn = self._get_connection()
        rows = conn.execute("SELECT * FROM agents").fetchall()
        return [
            Agent(
                id=r["id"],
                name=r["name"],
                status=AgentState(r["status"]),
                reserved_by_worker=r["reserved_by_worker"],
                reserved_at=r["reserved_at"],
                current_call_id=r["current_call_id"],
                version=r["version"],
                last_state_change=r["last_state_change"],
                skills=json.loads(r["skills"]),
            )
            for r in rows
        ]

    def update_agent_status(
        self,
        agent_id: str,
        new_status: AgentState,
        worker_id: Optional[str] = None,
        call_id: Optional[str] = None,
        expected_version: Optional[int] = None,
    ) -> bool:
        """
        Updates agent status. If expected_version is provided, performs Optimistic Concurrency Control (OCC).
        Returns True if updated, False if version conflict or agent not found.
        """
        conn = self._get_connection()
        now = time.time()
        sql = """
            UPDATE agents
            SET status = ?,
                reserved_by_worker = ?,
                current_call_id = ?,
                version = version + 1,
                last_state_change = ?
            WHERE id = ?
        """
        params = [new_status.value, worker_id, call_id, now, agent_id]
        if expected_version is not None:
            sql += " AND version = ?"
            params.append(expected_version)

        cursor = conn.execute(sql, params)
        return cursor.rowcount > 0

    def reserve_single_agent_atomic(
        self,
        agent_id: str,
        worker_id: str,
    ) -> bool:
        """
        Atomically attempts to reserve a specific agent.
        Prevents race condition: Only one worker can transition from AVAILABLE to RESERVED.
        """
        conn = self._get_connection()
        now = time.time()
        cursor = conn.execute(
            """
            UPDATE agents
            SET status = 'RESERVED',
                reserved_by_worker = ?,
                reserved_at = ?,
                version = version + 1,
                last_state_change = ?
            WHERE id = ? AND status = 'AVAILABLE'
            """,
            (worker_id, now, now, agent_id),
        )
        return cursor.rowcount > 0

    def reserve_available_agents_batch(
        self,
        worker_id: str,
        max_count: int,
    ) -> List[Agent]:
        """
        Atomically claims up to max_count AVAILABLE agents for a worker.
        Uses BEGIN IMMEDIATE to prevent concurrent workers from claiming the same agents.
        """
        if max_count <= 0:
            return []
        conn = self._get_connection()
        now = time.time()
        reserved: List[Agent] = []

        try:
            conn.execute("BEGIN IMMEDIATE")
            # Select available agents ordered by longest idle (fair scheduling)
            rows = conn.execute(
                """
                SELECT id, name, status, version, last_state_change, skills
                FROM agents
                WHERE status = 'AVAILABLE'
                ORDER BY last_state_change ASC
                LIMIT ?
                """,
                (max_count,),
            ).fetchall()

            for r in rows:
                c = conn.execute(
                    """
                    UPDATE agents
                    SET status = 'RESERVED',
                        reserved_by_worker = ?,
                        reserved_at = ?,
                        version = version + 1,
                        last_state_change = ?
                    WHERE id = ? AND status = 'AVAILABLE' AND version = ?
                    """,
                    (worker_id, now, now, r["id"], r["version"]),
                )
                if c.rowcount > 0:
                    reserved.append(
                        Agent(
                            id=r["id"],
                            name=r["name"],
                            status=AgentState.RESERVED,
                            reserved_by_worker=worker_id,
                            reserved_at=now,
                            version=r["version"] + 1,
                            last_state_change=now,
                            skills=json.loads(r["skills"]),
                        )
                    )
            conn.execute("COMMIT")
        except Exception:
            try:
                conn.execute("ROLLBACK")
            except Exception:
                pass
            raise

        return reserved

    def count_agents_by_status(self) -> Dict[AgentState, int]:
        conn = self._get_connection()
        rows = conn.execute("SELECT status, COUNT(*) as cnt FROM agents GROUP BY status").fetchall()
        result = {state: 0 for state in AgentState}
        for r in rows:
            try:
                result[AgentState(r["status"])] = r["cnt"]
            except ValueError:
                pass
        return result

    # ==========================================
    # BORROWER OPERATIONS & QUEUE
    # ==========================================

    def create_borrower(self, borrower: Borrower) -> None:
        conn = self._get_connection()
        conn.execute(
            """
            INSERT INTO borrowers (id, name, phone_number, campaign_id, status, priority, retry_count, max_retries, cooldown_seconds, last_attempt_at, version)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                borrower.id,
                borrower.name,
                borrower.phone_number,
                borrower.campaign_id,
                borrower.status.value,
                borrower.priority,
                borrower.retry_count,
                borrower.max_retries,
                borrower.cooldown_seconds,
                borrower.last_attempt_at,
                borrower.version,
            ),
        )

    def get_borrower(self, borrower_id: str) -> Optional[Borrower]:
        conn = self._get_connection()
        row = conn.execute("SELECT * FROM borrowers WHERE id = ?", (borrower_id,)).fetchone()
        if not row:
            return None
        return Borrower(
            id=row["id"],
            name=row["name"],
            phone_number=row["phone_number"],
            campaign_id=row["campaign_id"],
            status=BorrowerState(row["status"]),
            priority=row["priority"],
            retry_count=row["retry_count"],
            max_retries=row["max_retries"],
            cooldown_seconds=row["cooldown_seconds"],
            last_attempt_at=row["last_attempt_at"],
            version=row["version"],
        )

    def claim_next_borrowers_batch(
        self,
        campaign_id: str,
        max_count: int,
    ) -> List[Borrower]:
        """
        Atomically selects and marks eligible borrowers as IN_FLIGHT.
        Eligible = PENDING or (COOLDOWN and time elapsed >= cooldown_seconds and retry_count < max_retries).
        """
        if max_count <= 0:
            return []
        conn = self._get_connection()
        now = time.time()
        claimed: List[Borrower] = []

        try:
            conn.execute("BEGIN IMMEDIATE")
            rows = conn.execute(
                """
                SELECT id, name, phone_number, campaign_id, status, priority, retry_count, max_retries, cooldown_seconds, last_attempt_at, version
                FROM borrowers
                WHERE campaign_id = ?
                  AND (
                      status = 'PENDING'
                      OR (status = 'COOLDOWN' AND retry_count < max_retries AND (? - COALESCE(last_attempt_at, 0)) >= cooldown_seconds)
                  )
                ORDER BY priority DESC, id ASC
                LIMIT ?
                """,
                (campaign_id, now, max_count),
            ).fetchall()

            for r in rows:
                c = conn.execute(
                    """
                    UPDATE borrowers
                    SET status = 'IN_FLIGHT',
                        last_attempt_at = ?,
                        retry_count = retry_count + 1,
                        version = version + 1
                    WHERE id = ? AND version = ?
                    """,
                    (now, r["id"], r["version"]),
                )
                if c.rowcount > 0:
                    claimed.append(
                        Borrower(
                            id=r["id"],
                            name=r["name"],
                            phone_number=r["phone_number"],
                            campaign_id=r["campaign_id"],
                            status=BorrowerState.IN_FLIGHT,
                            priority=r["priority"],
                            retry_count=r["retry_count"] + 1,
                            max_retries=r["max_retries"],
                            cooldown_seconds=r["cooldown_seconds"],
                            last_attempt_at=now,
                            version=r["version"] + 1,
                        )
                    )
            conn.execute("COMMIT")
        except Exception:
            try:
                conn.execute("ROLLBACK")
            except Exception:
                pass
            raise

        return claimed

    def update_borrower_outcome(
        self,
        borrower_id: str,
        new_status: BorrowerState,
    ) -> None:
        conn = self._get_connection()
        conn.execute(
            """
            UPDATE borrowers
            SET status = ?, version = version + 1
            WHERE id = ?
            """,
            (new_status.value, borrower_id),
        )

    # ==========================================
    # CALL OPERATIONS & MONOTONIC STATE TRANSITIONS
    # ==========================================

    def create_call(self, call: Call) -> None:
        conn = self._get_connection()
        conn.execute(
            """
            INSERT INTO calls (
                id, borrower_id, campaign_id, status, agent_id, worker_id,
                provider_id, provider_call_id, phone_number, created_at,
                initiated_at, ringing_at, answered_at, connected_at, completed_at,
                lease_expires_at, abandoned, failure_reason, version
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                call.id,
                call.borrower_id,
                call.campaign_id,
                call.status.value,
                call.agent_id,
                call.worker_id,
                call.provider_id,
                call.provider_call_id,
                call.phone_number,
                call.created_at,
                call.initiated_at,
                call.ringing_at,
                call.answered_at,
                call.connected_at,
                call.completed_at,
                call.lease_expires_at,
                1 if call.abandoned else 0,
                call.failure_reason,
                call.version,
            ),
        )

    def get_call(self, call_id: str) -> Optional[Call]:
        conn = self._get_connection()
        row = conn.execute("SELECT * FROM calls WHERE id = ?", (call_id,)).fetchone()
        if not row:
            return None
        return Call(
            id=row["id"],
            borrower_id=row["borrower_id"],
            campaign_id=row["campaign_id"],
            status=CallState(row["status"]),
            agent_id=row["agent_id"],
            worker_id=row["worker_id"],
            provider_id=row["provider_id"],
            provider_call_id=row["provider_call_id"],
            phone_number=row["phone_number"],
            created_at=row["created_at"],
            initiated_at=row["initiated_at"],
            ringing_at=row["ringing_at"],
            answered_at=row["answered_at"],
            connected_at=row["connected_at"],
            completed_at=row["completed_at"],
            lease_expires_at=row["lease_expires_at"],
            abandoned=bool(row["abandoned"]),
            failure_reason=row["failure_reason"],
            version=row["version"],
        )

    def transition_call_state(
        self,
        call_id: str,
        new_state: CallState,
        agent_id: Optional[str] = None,
        provider_call_id: Optional[str] = None,
        reason: Optional[str] = None,
        abandoned: Optional[bool] = None,
        lease_duration: Optional[float] = None,
    ) -> Tuple[bool, Optional[Call]]:
        """
        Atomically validates monotonicity and updates call state in DB.
        Guarantees:
        1. Duplicate events are idempotent no-ops.
        2. Terminal calls (COMPLETED, FAILED, CANCELLED) cannot regress.
        3. Out-of-order events with lower rank are rejected.
        """
        conn = self._get_connection()
        now = time.time()

        try:
            conn.execute("BEGIN IMMEDIATE")
            call = self.get_call(call_id)
            if not call:
                conn.execute("ROLLBACK")
                return False, None

            # Attempt in-memory model transition (validates legal transition, idempotency, and ranking)
            changed = call.transition_to(
                new_state=new_state,
                agent_id=agent_id,
                provider_call_id=provider_call_id,
                reason=reason,
                abandoned=abandoned,
                timestamp=now,
            )

            if not changed:
                conn.execute("COMMIT")
                return False, call

            new_lease = (now + lease_duration) if lease_duration is not None else call.lease_expires_at

            conn.execute(
                """
                UPDATE calls
                SET status = ?,
                    agent_id = ?,
                    provider_call_id = ?,
                    initiated_at = ?,
                    ringing_at = ?,
                    answered_at = ?,
                    connected_at = ?,
                    completed_at = ?,
                    lease_expires_at = ?,
                    abandoned = ?,
                    failure_reason = ?,
                    version = version + 1
                WHERE id = ?
                """,
                (
                    call.status.value,
                    call.agent_id,
                    call.provider_call_id,
                    call.initiated_at,
                    call.ringing_at,
                    call.answered_at,
                    call.connected_at,
                    call.completed_at,
                    new_lease,
                    1 if call.abandoned else 0,
                    call.failure_reason,
                    call.id,
                ),
            )
            call.lease_expires_at = new_lease
            conn.execute("COMMIT")
            return True, call
        except Exception:
            try:
                conn.execute("ROLLBACK")
            except Exception:
                pass
            raise

    def count_calls_by_status(self, campaign_id: str) -> Dict[CallState, int]:
        conn = self._get_connection()
        rows = conn.execute(
            "SELECT status, COUNT(*) as cnt FROM calls WHERE campaign_id = ? GROUP BY status",
            (campaign_id,),
        ).fetchall()
        result = {state: 0 for state in CallState}
        for r in rows:
            try:
                result[CallState(r["status"])] = r["cnt"]
            except ValueError:
                pass
        return result

    def get_abandoned_calls_count(self, campaign_id: str) -> int:
        conn = self._get_connection()
        row = conn.execute(
            "SELECT COUNT(*) as cnt FROM calls WHERE campaign_id = ? AND abandoned = 1",
            (campaign_id,),
        ).fetchone()
        return row["cnt"] if row else 0

    # ==========================================
    # IDEMPOTENT EVENT JOURNAL
    # ==========================================

    def record_event_idempotent(
        self,
        event_id: str,
        call_id: str,
        event_type: str,
        sequence_num: int,
        idempotency_key: str,
        payload: Dict[str, Any],
    ) -> bool:
        """
        Records an event with strict idempotency.
        Returns True if newly recorded, False if duplicate.
        """
        conn = self._get_connection()
        now = time.time()
        try:
            conn.execute(
                """
                INSERT INTO call_events (id, call_id, event_type, sequence_num, idempotency_key, payload, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event_id,
                    call_id,
                    event_type,
                    sequence_num,
                    idempotency_key,
                    json.dumps(payload),
                    now,
                ),
            )
            return True
        except sqlite3.IntegrityError:
            # Duplicate event caught by idempotency_key UNIQUE constraint
            return False

    # ==========================================
    # LEASE & CRASH RECONCILIATION
    # ==========================================

    def refresh_worker_lease(self, worker_id: str, lease_duration: float = 5.0) -> None:
        conn = self._get_connection()
        now = time.time()
        new_lease = now + lease_duration
        conn.execute(
            """
            UPDATE calls
            SET lease_expires_at = ?
            WHERE worker_id = ? AND status NOT IN ('COMPLETED', 'FAILED', 'CANCELLED')
            """,
            (new_lease, worker_id),
        )

    def get_stuck_calls_with_expired_leases(self, now: Optional[float] = None) -> List[Call]:
        curr = now if now is not None else time.time()
        conn = self._get_connection()
        rows = conn.execute(
            """
            SELECT * FROM calls
            WHERE lease_expires_at IS NOT NULL
              AND lease_expires_at < ?
              AND status NOT IN ('COMPLETED', 'FAILED', 'CANCELLED')
            """,
            (curr,),
        ).fetchall()
        return [
            Call(
                id=r["id"],
                borrower_id=r["borrower_id"],
                campaign_id=r["campaign_id"],
                status=CallState(r["status"]),
                agent_id=r["agent_id"],
                worker_id=r["worker_id"],
                provider_id=r["provider_id"],
                provider_call_id=r["provider_call_id"],
                phone_number=r["phone_number"],
                created_at=r["created_at"],
                initiated_at=r["initiated_at"],
                ringing_at=r["ringing_at"],
                answered_at=r["answered_at"],
                connected_at=r["connected_at"],
                completed_at=r["completed_at"],
                lease_expires_at=r["lease_expires_at"],
                abandoned=bool(r["abandoned"]),
                failure_reason=r["failure_reason"],
                version=r["version"],
            )
            for r in rows
        ]

    def get_stale_reserved_agents(self, max_reserved_age_sec: float = 15.0, now: Optional[float] = None) -> List[Agent]:
        """Detect agents stuck in RESERVED state without an active call (e.g. worker crashed during setup)."""
        curr = now if now is not None else time.time()
        cutoff = curr - max_reserved_age_sec
        conn = self._get_connection()
        rows = conn.execute(
            """
            SELECT * FROM agents
            WHERE status = 'RESERVED'
              AND (reserved_at IS NULL OR reserved_at < ?)
              AND (current_call_id IS NULL OR current_call_id NOT IN (SELECT id FROM calls WHERE status NOT IN ('COMPLETED', 'FAILED', 'CANCELLED')))
            """,
            (cutoff,),
        ).fetchall()
        return [
            Agent(
                id=r["id"],
                name=r["name"],
                status=AgentState(r["status"]),
                reserved_by_worker=r["reserved_by_worker"],
                reserved_at=r["reserved_at"],
                current_call_id=r["current_call_id"],
                version=r["version"],
                last_state_change=r["last_state_change"],
                skills=json.loads(r["skills"]),
            )
            for r in rows
        ]

    # ==========================================
    # CAMPAIGN & METRICS
    # ==========================================

    def create_campaign(self, campaign: Campaign) -> None:
        conn = self._get_connection()
        conn.execute(
            """
            INSERT INTO campaigns (
                id, name, mode, is_active, target_abandonment_rate, max_dial_multiplier,
                default_talk_time_sec, default_ring_time_sec, default_wrap_up_sec,
                min_agent_floor, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                campaign.id,
                campaign.name,
                campaign.mode.value,
                1 if campaign.is_active else 0,
                campaign.target_abandonment_rate,
                campaign.max_dial_multiplier,
                campaign.default_talk_time_sec,
                campaign.default_ring_time_sec,
                campaign.default_wrap_up_sec,
                campaign.min_agent_floor,
                campaign.created_at,
            ),
        )

    def get_campaign(self, campaign_id: str) -> Optional[Campaign]:
        conn = self._get_connection()
        row = conn.execute("SELECT * FROM campaigns WHERE id = ?", (campaign_id,)).fetchone()
        if not row:
            return None
        return Campaign(
            id=row["id"],
            name=row["name"],
            mode=DialingMode(row["mode"]),
            is_active=bool(row["is_active"]),
            target_abandonment_rate=row["target_abandonment_rate"],
            max_dial_multiplier=row["max_dial_multiplier"],
            default_talk_time_sec=row["default_talk_time_sec"],
            default_ring_time_sec=row["default_ring_time_sec"],
            default_wrap_up_sec=row["default_wrap_up_sec"],
            min_agent_floor=row["min_agent_floor"],
            created_at=row["created_at"],
        )

    def log_pacing_decision(
        self,
        decision_id: str,
        campaign_id: str,
        mode: str,
        available_agents: int,
        connected_calls: int,
        ringing_calls: int,
        estimated_answer_rate: float,
        pacing_proposed_dials: int,
        safety_action: str,
        approved_dials: int,
        reason: str,
        timestamp: Optional[float] = None,
    ) -> None:
        conn = self._get_connection()
        now = timestamp if timestamp is not None else time.time()
        conn.execute(
            """
            INSERT INTO pacing_metrics (
                id, campaign_id, timestamp, mode, available_agents,
                connected_calls, ringing_calls, estimated_answer_rate,
                pacing_proposed_dials, safety_action, approved_dials, reason
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                decision_id,
                campaign_id,
                now,
                mode,
                available_agents,
                connected_calls,
                ringing_calls,
                estimated_answer_rate,
                pacing_proposed_dials,
                safety_action,
                approved_dials,
                reason,
            ),
        )
