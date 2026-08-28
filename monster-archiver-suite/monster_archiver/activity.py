"""Undo/recent-activity log (--revert-activity): reverts a single
activity_log row written by pipeline.py's archive step or album_merge.py's
merge step, both of which can move files unattended (Fix-All, Merge-All).

v1 scope: revert = move the file back to where it came from. There is no
tag-snapshot restore yet — nothing populates prior_tags_json today, so
implementing that side would be dead code; if a future caller starts
recording a tag snapshot, restoring it belongs here too.
"""
import os
import shutil

from . import db
from . import naming
from . import state

# Only these actions record a real prior_path a file can be moved back to.
_REVERTABLE_ACTIONS = {"archive", "merge"}


def run_revert(activity_id):
    """Revert one activity_log entry by id. Returns a summary dict; never
    raises — every failure path (missing entry, already reverted, missing
    file, unrevertable action) is reported in the dict instead."""
    row = db.get_activity_by_id(activity_id)
    if not row:
        return {"error": f"No activity entry with id {activity_id}"}

    if row.get("reverted"):
        return {"error": f"Activity {activity_id} was already reverted"}

    action = row.get("action")
    if action not in _REVERTABLE_ACTIONS:
        return {"error": f"Activity {activity_id}'s action ('{action}') can't be reverted"}

    file_path = row.get("file_path")
    prior_path = row.get("prior_path")

    if not file_path or not os.path.exists(file_path):
        return {"error": f"Can't revert — file no longer exists at {file_path}", "id": activity_id}

    if not prior_path:
        return {"error": f"Activity {activity_id} has no recorded prior location", "id": activity_id}

    os.makedirs(os.path.dirname(prior_path), exist_ok=True)
    dest = naming._unique_path(prior_path) if os.path.exists(prior_path) else prior_path

    shutil.move(file_path, dest)
    db.mark_activity_reverted(activity_id)
    state.console.print(f"[bold green]↩️  Reverted activity {activity_id}: {file_path} → {dest}[/bold green]")

    return {"reverted": True, "id": activity_id, "restored_path": dest}
