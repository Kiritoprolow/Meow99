"""Lưu thành phẩm. HubStorage -> Hugging Face Hub (dataset repo); LocalStorage -> thư mục (dev/test).

Quy tắc: thư mục đích outputs/<job_id>/attempt-<n>/ KHÔNG được tồn tại trước (không ghi đè vô ý);
một commit duy nhất cho mọi file; xác minh sau upload (file tồn tại + kích thước khớp) rồi mới báo xong.
"""
from __future__ import annotations

import shutil
import time
from pathlib import Path
from typing import Any, Protocol

HF = "https://huggingface.co"


class StorageError(RuntimeError):
    pass


class OutputExists(StorageError):
    pass


class OutputStorage(Protocol):
    def upload(self, job_id: str, attempt: int, files: dict[str, Path]) -> dict[str, Any]: ...


def prefix_for(job_id: str, attempt: int) -> str:
    return f"outputs/{job_id}/attempt-{attempt}"


class LocalStorage:
    def __init__(self, root: str | Path):
        self.root = Path(root).resolve()

    def upload(self, job_id: str, attempt: int, files: dict[str, Path]) -> dict[str, Any]:
        dest = self.root / prefix_for(job_id, attempt)
        if dest.exists():
            raise OutputExists(f"{prefix_for(job_id, attempt)} đã tồn tại, từ chối ghi đè")
        dest.mkdir(parents=True)
        entries, urls = [], {}
        for name, src in files.items():
            shutil.copy2(src, dest / name)
            if (dest / name).stat().st_size != src.stat().st_size:
                raise StorageError(f"Kích thước {name} không khớp sau khi lưu")
            entries.append({"path": f"{prefix_for(job_id, attempt)}/{name}", "size": src.stat().st_size})
            urls[name] = (dest / name).as_uri()
        return {"repo_id": "local", "revision": None, "access": "local", "prefix": prefix_for(job_id, attempt),
                "urls": urls, "files": entries}


class HubStorage:
    def __init__(self, token: str, repo_id: str, retries: int = 3, sleep=time.sleep):
        if not token or not repo_id:
            raise StorageError("Thiếu HF_TOKEN hoặc HF_OUTPUT_REPO")
        self._token, self.repo_id, self._retries, self._sleep = token, repo_id, retries, sleep

    def _api(self):
        try:
            from huggingface_hub import HfApi
        except ImportError as exc:
            raise StorageError("Thiếu thư viện huggingface_hub") from exc
        return HfApi(token=self._token)

    def upload(self, job_id: str, attempt: int, files: dict[str, Path]) -> dict[str, Any]:
        from huggingface_hub import CommitOperationAdd  # import trễ

        api = self._api()
        prefix = prefix_for(job_id, attempt)
        # repo phải tồn tại; nếu chưa có thì tạo PRIVATE (mặc định an toàn)
        try:
            info = api.repo_info(self.repo_id, repo_type="dataset")
        except Exception as exc:  # noqa: BLE001
            if "404" in str(exc) or type(exc).__name__ in {"RepositoryNotFoundError"}:
                api.create_repo(self.repo_id, repo_type="dataset", private=True, exist_ok=True)
                info = api.repo_info(self.repo_id, repo_type="dataset")
            else:
                raise StorageError(f"Không truy cập được repo output: {type(exc).__name__}") from None

        try:
            existing = list(api.list_repo_tree(self.repo_id, path_in_repo=prefix, repo_type="dataset", recursive=False))
        except Exception as exc:  # noqa: BLE001
            # thư mục chưa tồn tại -> Hub trả 404 (EntryNotFound); lỗi khác thì dừng, không đoán
            if type(exc).__name__ in {"EntryNotFoundError", "RemoteEntryNotFoundError"} or "404" in str(exc):
                existing = []
            else:
                raise StorageError(f"Không kiểm tra được thư mục đích: {type(exc).__name__}") from None
        if existing:
            raise OutputExists(f"{prefix} đã tồn tại trên Hub, từ chối ghi đè")

        ops = [CommitOperationAdd(path_in_repo=f"{prefix}/{name}", path_or_fileobj=str(p)) for name, p in files.items()]
        last: Exception | None = None
        commit = None
        for i in range(self._retries):
            try:
                commit = api.create_commit(self.repo_id, ops, repo_type="dataset",
                                           commit_message=f"{job_id} attempt-{attempt}")
                break
            except Exception as exc:  # noqa: BLE001
                last = exc
                if i + 1 < self._retries:
                    self._sleep(2 ** (i + 1))
        if commit is None:
            raise StorageError(f"Upload lên Hub thất bại sau {self._retries} lần: {type(last).__name__}")

        revision = commit.oid
        # xác minh: từng file có mặt và đúng kích thước
        entries = []
        paths = [f"{prefix}/{n}" for n in files]
        got = {x.path: x for x in api.get_paths_info(self.repo_id, paths, repo_type="dataset", revision=revision)}
        for name, p in files.items():
            rel = f"{prefix}/{name}"
            if rel not in got or got[rel].size != p.stat().st_size:
                raise StorageError(f"Xác minh upload thất bại cho {name}")
            entries.append({"path": rel, "size": p.stat().st_size})
        access = "private" if info.private else "public"
        urls = {name: f"{HF}/datasets/{self.repo_id}/resolve/{revision}/{prefix}/{name}" for name in files}
        return {"repo_id": self.repo_id, "revision": revision, "access": access, "prefix": prefix,
                "urls": urls, "files": entries}
