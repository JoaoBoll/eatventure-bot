"""Consulta e exclusão segura de amostras do dataset para o painel web."""

from collections import defaultdict
from pathlib import Path
import threading

from dataset.training_files import cleanup_records, snapshot_records


class DatasetManager:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self._lock = threading.Lock()
        self._records = None
        self._by_session = None
        self._images = None

    @staticmethod
    def _key(record):
        return (str(record.get("created_at") or ""), str(record.get("id") or ""))

    def _load(self, refresh=False):
        with self._lock:
            if refresh or self._records is None:
                records, _ = snapshot_records(self.root)
                records = sorted(records, key=self._key)
                grouped = defaultdict(list)
                images = {}
                for record in records:
                    session = record.get("session")
                    if not isinstance(session, str) or not session:
                        continue
                    grouped[session].append(record)
                    identifier = str(record.get("id") or Path(record["_path"]).stem)
                    images[(session, record.get("_shard"), identifier)] = record
                self._by_session = grouped
                self._images = images
                self._records = records
            return self._records

    def sessions(self, resolution=None, session_id=None):
        self._load(refresh=True)
        result = []
        for session, records in self._by_session.items():
            if session_id is not None and session != session_id:
                continue
            if resolution is not None:
                records = [record for record in records if record.get("_shard") == resolution]
            if not records:
                continue
            result.append({
                "id": session,
                "count": len(records),
                "created_at": records[0].get("created_at"),
                "updated_at": records[-1].get("created_at"),
                "device_id": next((item.get("device_id") for item in records if item.get("device_id")), None),
                "resolution": records[-1].get("_shard"),
                "resolutions": sorted({str(item.get("_shard")) for item in records if item.get("_shard")}),
            })
        return sorted(result, key=lambda item: (item["created_at"] or "", item["id"]), reverse=True)

    def records(self, session, resolution=None):
        self._load()
        records = list(self._by_session.get(session, ()))
        if resolution is not None:
            records = [record for record in records if record.get("_shard") == resolution]
        return records

    def page(self, session, offset=0, limit=200, resolution=None):
        records = self.records(session, resolution)
        if not records:
            raise KeyError(session)
        page = records[offset:offset + limit]
        return {
            "session": session,
            "total": len(records),
            "offset": offset,
            "limit": limit,
            "items": [
                {
                    "id": str(record.get("id") or Path(record["_path"]).stem),
                    "index": offset + index + 1,
                    "created_at": record.get("created_at"),
                    "action": record.get("action"),
                    "resolution": record.get("_shard"),
                }
                for index, record in enumerate(page)
            ],
        }

    def image(self, session, identifier, resolution=None):
        self._load()
        if resolution is None:
            record = next((
                item for (item_session, _, item_id), item in self._images.items()
                if item_session == session and item_id == identifier
            ), None)
        else:
            record = self._images.get((session, resolution, identifier))
        if record is not None:
            path = Path(record["_path"]).resolve()
            if path.is_relative_to(self.root) and path.is_file():
                return path
        raise KeyError(identifier)

    def delete(self, session, start=None, end=None, delete_all=False, resolution=None):
        records = self.records(session, resolution)
        if not records:
            raise KeyError(session)
        if delete_all:
            selected = records
        else:
            if type(start) is not int or type(end) is not int or start < 1 or end < start or end > len(records):
                raise ValueError(f"Informe um intervalo entre 1 e {len(records)}.")
            selected = records[start - 1:end]
        identifiers = {str(record.get("id") or Path(record["_path"]).stem) for record in selected}
        result = cleanup_records(self.root, selected, identifiers)
        with self._lock:
            self._records = None
            self._by_session = None
            self._images = None
        if result["errors"]:
            raise RuntimeError("A limpeza não foi concluída: " + "; ".join(result["errors"]))
        return {
            "session": session,
            "deleted": result["records_removed"],
            "images_deleted": result["images_removed"],
            "bytes_freed": result["bytes_freed"],
        }
