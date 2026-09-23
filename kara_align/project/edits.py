"""Manual corrections on an alignment result.

The raw model prediction (``model_start_ms``/``model_end_ms``) is never
modified.  Manual edits are stored in ``UnitTiming.manual`` and every change
is appended to ``manual_history`` so earlier states can be restored.
"""

from __future__ import annotations

from typing import Optional

from ..models import AlignmentResult, ManualEdit, UnitTiming


class EditError(ValueError):
    pass


def _unit(result: AlignmentResult, unit_id: str) -> UnitTiming:
    for ut in result.units:
        if ut.unit_id == unit_id:
            return ut
    raise EditError(f"结果中没有单元 {unit_id}")


def _auto_times(ut: UnitTiming) -> tuple[Optional[int], Optional[int]]:
    """Times without manual override (model prediction after tail correction)."""
    if ut.tail is not None:
        return ut.model_start_ms, ut.tail.new_end_ms
    return ut.model_start_ms, ut.model_end_ms


def apply_final_times(ut: UnitTiming) -> None:
    if ut.manual is not None:
        ut.start_ms, ut.end_ms = ut.manual.start_ms, ut.manual.end_ms
        if ut.start_ms is not None and ut.end_ms is not None and ut.status != "ok":
            # the original failure reason stays visible in the flags
            ut.flags.append(f"manual-resolved:{ut.status}")
            ut.status = "ok"
            ut.reason = None
    else:
        ut.start_ms, ut.end_ms = _auto_times(ut)


def set_manual(result: AlignmentResult, unit_id: str, start_ms: Optional[int], end_ms: Optional[int],
               *, locked: bool = True, note: str = "", duration_ms: Optional[int] = None) -> UnitTiming:
    ut = _unit(result, unit_id)
    for v in (start_ms, end_ms):
        if v is not None and v < 0:
            raise EditError("时间不能为负")
        if v is not None and duration_ms is not None and v > duration_ms:
            raise EditError("时间超出音频长度")
    if start_ms is not None and end_ms is not None and end_ms <= start_ms:
        raise EditError("结束时间必须大于开始时间（区间为 [start, end)）")
    edit = ManualEdit(start_ms=start_ms, end_ms=end_ms, locked=locked, note=note)
    ut.manual = edit
    ut.manual_history.append(edit)
    if "manual" not in ut.flags:
        ut.flags.append("manual")
    apply_final_times(ut)
    _refresh_line(result, ut.line_id)
    return ut


def set_lock(result: AlignmentResult, unit_id: str, locked: bool) -> UnitTiming:
    ut = _unit(result, unit_id)
    if ut.manual is None:
        if not locked:
            return ut
        # lock the current (model) times as a manual confirmation
        return set_manual(result, unit_id, ut.start_ms, ut.end_ms, locked=True, note="锁定当前时间")
    edit = ut.manual.model_copy(update={"locked": locked})
    ut.manual = edit
    ut.manual_history.append(edit)
    return ut


def clear_manual(result: AlignmentResult, unit_id: str) -> UnitTiming:
    """Drop the manual override (history kept) and go back to the model time."""
    ut = _unit(result, unit_id)
    if ut.manual is not None:
        ut.manual_history.append(ManualEdit(start_ms=None, end_ms=None, locked=False, note="清除人工修改"))
    ut.manual = None
    if "manual" in ut.flags:
        ut.flags.remove("manual")
    apply_final_times(ut)
    if ut.start_ms is None and ut.model_start_ms is None and ut.status == "ok":
        ut.status = "failed"
    _refresh_line(result, ut.line_id)
    return ut


def restore_manual(result: AlignmentResult, unit_id: str, edit: Optional[dict]) -> UnitTiming:
    """Set the manual state exactly (used by UI undo/redo)."""
    if edit is None:
        return clear_manual(result, unit_id)
    e = ManualEdit.model_validate(edit)
    return set_manual(result, unit_id, e.start_ms, e.end_ms, locked=e.locked, note=e.note or "撤销/重做")


def _refresh_line(result: AlignmentResult, line_id: str) -> None:
    units = [u for u in result.units if u.line_id == line_id]
    starts = [u.start_ms for u in units if u.start_ms is not None]
    ends = [u.end_ms for u in units if u.end_ms is not None]
    for lt in result.lines:
        if lt.line_id == line_id:
            lt.start_ms = min(starts) if starts else None
            lt.end_ms = max(ends) if ends else None


def manual_count(result: AlignmentResult) -> int:
    return sum(1 for u in result.units if u.manual is not None)
