"""Correlated piano transactions; shared by MCP tools and bank workflows."""

import json
import math
import time
import uuid
from contextlib import ExitStack, contextmanager

from fl_studio_mcp.utils.fl_trigger import get_trigger
from fl_studio_mcp.utils.note_protocol import PROTOCOL, normalize_requests, request_digest
from fl_studio_mcp.utils.paths import get_fl_settings_dir
from fl_studio_mcp.utils.protocol import atomic_json, transaction_lock


class PianoBridge:
    def __init__(self, directory=None, trigger=None, timeout=5):
        self.directory = directory or get_fl_settings_dir() / "Piano roll scripts"
        self.queue = self.directory / "mcp_request.json"
        self.response = self.directory / "mcp_response.json"
        self.verification = self.directory / "mcp-verification.json"
        self.trigger = trigger or get_trigger()
        self.select_compose = trigger is None and self.trigger.platform == "Windows"
        self.timeout = timeout

    def dispatch(self):
        if self.select_compose:
            # Selecting the exact script executes it once. Never send the last-script
            # shortcut as a fallback: another selected script could edit this score.
            from fl_studio_mcp.utils.piano_ready import dispatch_compose

            return dispatch_compose()
        return self.trigger.trigger(delay=0)

    def current_target(self):
        from fl_studio_mcp.utils.piano_ready import selected_identity

        # Keep the entire identity contract: the selector can also include project
        # identity without changing the transaction protocol again.
        return list(selected_identity())

    def verify_target(self, envelope):
        expected = envelope.get("target_identity")
        if not isinstance(expected, list) or not expected or self.current_target() != expected:
            raise RuntimeError("Piano target changed since inspection; no further dispatch")

    def recover_expired_read(self):
        """Retire only an expired read; caller owns the piano session's locks.

        A failed menu opening can leave a harmless read without a receipt. Never
        let that case require manual JSON deletion, and never retire a write or
        obscure uncertain execution/recovery evidence.
        """
        if self.verification.exists():
            return {"recovered": False, "reason": "verification_pending"}
        envelope = self.read_queue()
        if envelope == []:
            return {"recovered": False, "reason": "empty"}
        if not isinstance(envelope, dict) or envelope.get("protocol") != PROTOCOL:
            return {"recovered": False, "reason": "malformed_request"}
        request_id = envelope.get("id")
        if (not isinstance(request_id, str) or len(request_id) != 32
                or any(char not in "0123456789abcdef" for char in request_id)):
            return {"recovered": False, "reason": "malformed_request"}
        if envelope.get("requests") != []:
            return {"recovered": False, "reason": "not_read_only"}
        expires_at = envelope.get("expires_at")
        if (type(expires_at) not in (int, float) or not math.isfinite(expires_at)):
            return {"recovered": False, "reason": "malformed_request"}
        if expires_at >= time.time():
            return {"recovered": False, "reason": "not_expired"}
        try:
            digest = request_digest(envelope)
        except (TypeError, ValueError):
            return {"recovered": False, "reason": "malformed_request"}
        receipts = self.directory / "mcp-receipts"
        for path in (self.response, receipts / (request_id + ".json")):
            try:
                receipt = json.loads(path.read_text(encoding="utf-8"))
            except FileNotFoundError:
                continue
            except (OSError, ValueError):
                return {"recovered": False, "reason": "malformed_receipt"}
            if (not isinstance(receipt, dict) or receipt.get("protocol") != PROTOCOL
                    or not isinstance(receipt.get("id"), str)
                    or len(receipt["id"]) != 32
                    or any(char not in "0123456789abcdef" for char in receipt["id"])
                    or receipt.get("status") not in ("success", "error", "applying")
                    or receipt.get("outcome") not in
                    ("verified", "restored", "not_applied", "unknown")):
                return {"recovered": False, "reason": "malformed_receipt"}
            if receipt["status"] == "applying" or receipt["outcome"] == "unknown":
                return {"recovered": False, "reason": "unresolved_receipt"}
            receipt_hash = receipt.get("request_hash")
            if (type(receipt.get("success")) is not bool or not isinstance(receipt_hash, str)
                    or len(receipt_hash) != 64
                    or any(char not in "0123456789abcdef" for char in receipt_hash)):
                return {"recovered": False, "reason": "malformed_receipt"}
            if receipt["status"] == "success":
                state = receipt.get("state")
                if (not receipt["success"] or receipt["outcome"] != "verified"
                        or not isinstance(state, dict) or state.get("id") != receipt["id"]
                        or not state.get("revision")):
                    return {"recovered": False, "reason": "malformed_receipt"}
            elif receipt["success"] or receipt["outcome"] not in ("not_applied", "restored"):
                return {"recovered": False, "reason": "malformed_receipt"}
            if receipt["id"] == request_id and receipt.get("request_hash") != digest:
                return {"recovered": False, "reason": "receipt_identity_mismatch"}
        receipts.mkdir(exist_ok=True)
        receipt_path = receipts / (request_id + ".expired-read.json")
        atomic_json(receipt_path, {
            "protocol": PROTOCOL, "id": request_id, "request_hash": digest,
            "status": "cancelled", "outcome": "expired_read_cancelled",
            "requests": [], "target_identity": envelope.get("target_identity"),
            "expires_at": expires_at, "cancelled_at": time.time(),
            "reason": "Expired read-only request retired; no score mutation was requested",
        })
        atomic_json(self.queue, [])
        return {"recovered": True, "id": request_id, "receipt_file": str(receipt_path)}

    def read_queue(self):
        try:
            return json.loads(self.queue.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return []
        except (OSError, ValueError) as exc:
            raise RuntimeError("Unreadable piano queue; inspect it before continuing") from exc

    def result(self, request_id):
        try:
            result = json.loads(self.response.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if (
            isinstance(result, dict)
            and result.get("id") == request_id
            and result.get("protocol") == PROTOCOL
        ):
            return result
        return None

    def acknowledge(self, result):
        receipts = self.directory / "mcp-receipts"
        receipts.mkdir(exist_ok=True)
        atomic_json(receipts / (result["id"] + ".json"), result)
        atomic_json(self.queue, [])

    def wait(self, envelope):
        deadline = time.monotonic() + self.timeout
        while time.monotonic() < deadline:
            result = self.result(envelope["id"])
            if result and result.get("status") in ("success", "error"):
                if result.get("request_hash") != request_digest(envelope):
                    raise RuntimeError("Response payload identity mismatch; request retained")
                state = result.get("state", {})
                if result.get("success") and (
                    state.get("id") != envelope["id"]
                    or not state.get("revision")
                    or result.get("outcome") != "verified"
                ):
                    raise RuntimeError("Matching response lacks verified score state")
                if result.get("outcome") != "unknown":
                    if result.get("success") and envelope["requests"]:
                        # Keep a durable write barrier while a separate invocation
                        # observes FL's committed state. Reads remain available.
                        atomic_json(
                            self.verification,
                            {
                                "id": envelope["id"],
                                "status": "verification_pending",
                                "result": result,
                            },
                        )
                    self.acknowledge(result)
                if not result.get("success"):
                    raise RuntimeError(json.dumps(result))
                return result
            time.sleep(0.01)
        raise TimeoutError(
            f"Piano request {envelope['id']} unresolved; queue retained. "
            "Inspect the receipt and automatic ComposeWithLLM activation before retrying."
        )

    def exchange(self, requests=None, state=None, auto_trigger=True):
        requests = requests if requests is not None else []
        if requests and self.verification.exists():
            raise RuntimeError(
                "Unresolved committed-state verification; "
                "inspect mcp-verification.json before editing"
            )
        if requests and state is None:
            raise ValueError("Mutation requires a freshly inspected score")
        normalized = normalize_requests(requests, state["ppq"] if state else 96)
        self.recover_expired_read()
        if self.read_queue() != []:
            raise RuntimeError("Piano queue contains unresolved work; refusing to overwrite")
        target = self.current_target() if self.select_compose else None
        if requests and self.select_compose and state.get("target_identity") != target:
            raise RuntimeError("Piano target changed since inspection; no notes dispatched")
        expiry_seconds = max(self.timeout, 45) if self.select_compose else self.timeout
        envelope = {
            "protocol": PROTOCOL,
            "id": uuid.uuid4().hex,
            "expires_at": time.time() + (expiry_seconds if auto_trigger else 60),
            "requests": normalized,
        }
        if state:
            envelope.update(ppq=state["ppq"], expected_revision=state["revision"])
        if self.select_compose:
            envelope["target_identity"] = target
        atomic_json(self.queue, envelope)
        if not auto_trigger:
            return {
                "success": True,
                "status": "queued",
                "outcome": "not_dispatched",
                "id": envelope["id"],
                "expires_at": envelope["expires_at"],
            }
        if not self.dispatch():
            raise RuntimeError(
                "ComposeWithLLM dispatch unverified; request retained for inspection"
            )
        return self.complete(envelope)

    def complete(self, envelope):
        result = self.wait(envelope)
        if envelope["requests"]:
            try:
                if self.select_compose:
                    self.verify_target(envelope)
                settled = self.exchange()["state"]
                if settled["revision"] != result["state"]["revision"]:
                    raise RuntimeError("Committed score differs from script readback")
                if (self.select_compose
                        and settled.get("target_identity") != envelope["target_identity"]):
                    raise RuntimeError("Piano target changed during committed-state verification")
                result["state"] = dict(settled, id=result["id"], observed_by=settled["id"])
                result["committed_readback"] = True
            except Exception as exc:
                result.update(success=False, status="error", outcome="unknown", error=str(exc))
                atomic_json(
                    self.verification, {"id": result["id"], "status": "unknown", "result": result}
                )
                atomic_json(self.directory / "mcp-receipts" / (result["id"] + ".json"), result)
                raise RuntimeError(json.dumps(result)) from exc
            atomic_json(self.directory / "mcp-receipts" / (result["id"] + ".json"), result)
            self.verification.unlink()
        elif self.select_compose:
            self.verify_target(envelope)
            result["state"]["target_identity"] = list(envelope["target_identity"])
            atomic_json(self.directory / "mcp-receipts" / (result["id"] + ".json"), result)
        return result

    def trigger_pending(self):
        envelope = self.read_queue()
        if envelope == []:
            return self.exchange()
        if not isinstance(envelope, dict) or envelope.get("protocol") != PROTOCOL:
            raise RuntimeError("Legacy or malformed queue requires explicit inspection")
        request_id = envelope.get("id")
        if (not isinstance(request_id, str) or len(request_id) != 32
                or any(char not in "0123456789abcdef" for char in request_id)):
            raise RuntimeError("Malformed pending request ID; queue retained for inspection")
        prior = self.result(envelope["id"])
        if prior:
            if prior.get("status") == "applying" or prior.get("outcome") == "unknown":
                raise RuntimeError("Previous request outcome unknown; do not replay")
            return self.complete(envelope)
        expires_at = envelope.get("expires_at")
        if (type(expires_at) not in (int, float) or not math.isfinite(expires_at)):
            raise RuntimeError("Malformed pending expiry; queue retained for inspection")
        if expires_at < time.time():
            raise RuntimeError("Pending piano request expired; queue retained without dispatch")
        normalize_requests(envelope.get("requests"), envelope.get("ppq", 96))
        if envelope["requests"] and self.verification.exists():
            raise RuntimeError("Unresolved committed-state verification; pending request retained")
        if self.select_compose:
            self.verify_target(envelope)
        if not self.dispatch():
            raise RuntimeError("ComposeWithLLM dispatch unverified; queue retained")
        return self.complete(envelope)


@contextmanager
def piano_session():
    bridge = PianoBridge()
    bridge.directory.mkdir(parents=True, exist_ok=True)
    with ExitStack() as stack:
        if bridge.select_compose:
            from fl_studio_mcp.utils.midi_connection import get_connection

            # Keep selection, note edits and independent readback serialized with all
            # other FL GUI workflows. The selector runs synchronously on this thread.
            stack.enter_context(
                transaction_lock(get_connection()._hardware_dir / "mcp-gui.lock", 5)
            )
        stack.enter_context(transaction_lock(bridge.directory / "pattern-bank.lock", 5))
        yield bridge


def edit_notes(requests, auto_trigger=True):
    # Validate malformed values before triggering any UI. Actual PPQ is read below.
    normalize_requests(requests, 96000)
    with piano_session() as bridge:
        state = bridge.exchange()["state"]
        return bridge.exchange(requests, state, auto_trigger)
