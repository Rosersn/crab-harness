"""Tests for the uploads router (BOS + PG metadata + local dir + sandbox sync)."""

import asyncio
import uuid
from io import BytesIO
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import UploadFile

from app.gateway.routers import uploads


def _fake_user(user_id=None, tenant_id=None):
    """Create a fake AuthenticatedUser."""
    user = MagicMock()
    user.user_id = user_id or uuid.uuid4()
    user.tenant_id = tenant_id or uuid.uuid4()
    user.email = "test@example.com"
    user.role = "member"
    return user


def _fake_db():
    """Create a fake AsyncSession."""
    db = AsyncMock()
    db.commit = AsyncMock()
    return db


def _fake_storage():
    """Create a fake ObjectStorage."""
    storage = AsyncMock()
    storage.put = AsyncMock(return_value="key")
    storage.get = AsyncMock()
    storage.delete = AsyncMock()
    return storage


def _fake_upload_record(upload_id=None, filename="notes.txt", size_bytes=13, bos_key="k", markdown_bos_key=None):
    """Create a fake UploadMetadata record."""
    record = MagicMock()
    record.id = upload_id or uuid.uuid4()
    record.filename = filename
    record.size_bytes = size_bytes
    record.bos_key = bos_key
    record.markdown_bos_key = markdown_bos_key
    return record


def _fake_thread_record(thread_id=None, user_id=None):
    """Create a fake Thread record."""
    record = MagicMock()
    record.id = thread_id or uuid.uuid4()
    record.user_id = user_id or uuid.uuid4()
    return record


def _sandbox_mocks(sandbox_id="local"):
    """Return (provider_mock, sandbox_mock) for sandbox patching."""
    provider = MagicMock()
    provider.acquire.return_value = sandbox_id
    sandbox = MagicMock()
    provider.get.return_value = sandbox
    return provider, sandbox


def test_upload_files_writes_to_bos_pg_and_local(tmp_path):
    """Upload stores file in BOS, PG, and local thread dir."""
    user = _fake_user()
    db = _fake_db()
    storage = _fake_storage()
    upload_record = _fake_upload_record(filename="notes.txt", size_bytes=13)
    provider, sandbox = _sandbox_mocks("local")
    thread_repo_mock = MagicMock()
    thread_repo_mock.get = AsyncMock(return_value=_fake_thread_record(user_id=user.user_id))
    thread_repo_mock.create = AsyncMock()

    repo_mock = MagicMock()
    repo_mock.list_for_thread = AsyncMock(return_value=[])
    repo_mock.create = AsyncMock(return_value=upload_record)

    with (
        patch.object(uploads, "get_object_storage", return_value=storage),
        patch("app.gateway.routers.uploads.UploadRepo", return_value=repo_mock),
        patch("app.gateway.routers.uploads.ThreadRepo", return_value=thread_repo_mock),
        patch.object(uploads, "ensure_uploads_dir", return_value=tmp_path),
        patch.object(uploads, "get_sandbox_provider", return_value=provider),
    ):
        file = UploadFile(filename="notes.txt", file=BytesIO(b"hello uploads"))
        result = asyncio.run(
            uploads.upload_files(
                thread_id=str(uuid.uuid4()),
                files=[file],
                user=user,
                db=db,
            )
        )

    assert result.success is True
    assert len(result.files) == 1
    assert result.files[0]["filename"] == "notes.txt"
    assert result.files[0]["size"] == "13"

    # BOS put was called
    storage.put.assert_called_once()
    # PG create was called
    repo_mock.create.assert_called_once()
    # Local file was written
    assert (tmp_path / "notes.txt").read_bytes() == b"hello uploads"
    # Local sandbox — no sandbox.update_file
    sandbox.update_file.assert_not_called()
    # Session was committed (once after thread ownership check, once after upload)
    assert db.commit.call_count == 2


def test_upload_files_creates_missing_thread_before_metadata_write(tmp_path):
    """Uploading to a draft thread should materialize the thread row first."""
    user = _fake_user()
    db = _fake_db()
    tid = uuid.uuid4()
    storage = _fake_storage()
    upload_record = _fake_upload_record(filename="notes.txt", size_bytes=13)
    provider, sandbox = _sandbox_mocks("local")

    upload_repo_mock = MagicMock()
    upload_repo_mock.list_for_thread = AsyncMock(return_value=[])
    upload_repo_mock.create = AsyncMock(return_value=upload_record)

    thread_repo_mock = MagicMock()
    thread_repo_mock.get = AsyncMock(return_value=None)
    thread_repo_mock.create = AsyncMock(return_value=MagicMock(id=tid, user_id=user.user_id))

    with (
        patch.object(uploads, "get_object_storage", return_value=storage),
        patch("app.gateway.routers.uploads.UploadRepo", return_value=upload_repo_mock),
        patch("app.gateway.routers.uploads.ThreadRepo", return_value=thread_repo_mock),
        patch.object(uploads, "ensure_uploads_dir", return_value=tmp_path),
        patch.object(uploads, "get_sandbox_provider", return_value=provider),
    ):
        file = UploadFile(filename="notes.txt", file=BytesIO(b"hello uploads"))
        result = asyncio.run(
            uploads.upload_files(
                thread_id=str(tid),
                files=[file],
                user=user,
                db=db,
            )
        )

    assert result.success is True
    thread_repo_mock.create.assert_awaited_once_with(
        id=tid,
        tenant_id=user.tenant_id,
        user_id=user.user_id,
    )
    upload_repo_mock.create.assert_awaited_once()


def test_upload_files_syncs_non_local_sandbox(tmp_path):
    """Upload syncs files to non-local sandbox."""
    user = _fake_user()
    db = _fake_db()
    storage = _fake_storage()
    upload_record = _fake_upload_record(filename="notes.txt", size_bytes=13)
    provider, sandbox = _sandbox_mocks("aio-1")
    thread_repo_mock = MagicMock()
    thread_repo_mock.get = AsyncMock(return_value=_fake_thread_record(user_id=user.user_id))
    thread_repo_mock.create = AsyncMock()

    repo_mock = MagicMock()
    repo_mock.list_for_thread = AsyncMock(return_value=[])
    repo_mock.create = AsyncMock(return_value=upload_record)

    with (
        patch.object(uploads, "get_object_storage", return_value=storage),
        patch("app.gateway.routers.uploads.UploadRepo", return_value=repo_mock),
        patch("app.gateway.routers.uploads.ThreadRepo", return_value=thread_repo_mock),
        patch.object(uploads, "ensure_uploads_dir", return_value=tmp_path),
        patch.object(uploads, "get_sandbox_provider", return_value=provider),
        patch.object(uploads, "_make_file_sandbox_writable"),
    ):
        file = UploadFile(filename="notes.txt", file=BytesIO(b"hello uploads"))
        result = asyncio.run(
            uploads.upload_files(
                thread_id=str(uuid.uuid4()),
                files=[file],
                user=user,
                db=db,
            )
        )

    assert result.success is True
    sandbox.update_file.assert_called_once()


def test_upload_files_with_markdown_conversion(tmp_path):
    """Upload of convertible file stores both original and markdown."""
    user = _fake_user()
    db = _fake_db()
    storage = _fake_storage()
    upload_record = _fake_upload_record(filename="report.pdf", size_bytes=8)
    provider, sandbox = _sandbox_mocks("local")
    thread_repo_mock = MagicMock()
    thread_repo_mock.get = AsyncMock(return_value=_fake_thread_record(user_id=user.user_id))
    thread_repo_mock.create = AsyncMock()

    repo_mock = MagicMock()
    repo_mock.list_for_thread = AsyncMock(return_value=[])
    repo_mock.create = AsyncMock(return_value=upload_record)

    async def fake_convert(file_path: Path) -> Path:
        md_path = file_path.with_suffix(".md")
        md_path.write_text("converted", encoding="utf-8")
        return md_path

    with (
        patch.object(uploads, "get_object_storage", return_value=storage),
        patch("app.gateway.routers.uploads.UploadRepo", return_value=repo_mock),
        patch("app.gateway.routers.uploads.ThreadRepo", return_value=thread_repo_mock),
        patch.object(uploads, "ensure_uploads_dir", return_value=tmp_path),
        patch.object(uploads, "get_sandbox_provider", return_value=provider),
        patch("app.gateway.routers.uploads.convert_file_to_markdown", AsyncMock(side_effect=fake_convert)),
    ):
        file = UploadFile(filename="report.pdf", file=BytesIO(b"pdf-data"))
        result = asyncio.run(
            uploads.upload_files(
                thread_id=str(uuid.uuid4()),
                files=[file],
                user=user,
                db=db,
            )
        )

    assert result.success is True
    assert len(result.files) == 1
    file_info = result.files[0]
    assert file_info["filename"] == "report.pdf"
    assert file_info["markdown_file"] == "report.pdf.extracted.md"

    # BOS put called twice (original + markdown)
    assert storage.put.call_count == 2


def test_upload_files_rejects_unsafe_filenames(tmp_path):
    """Files with traversal filenames are skipped safely."""
    user = _fake_user()
    db = _fake_db()
    storage = _fake_storage()
    provider, sandbox = _sandbox_mocks("local")
    thread_repo_mock = MagicMock()
    thread_repo_mock.get = AsyncMock(return_value=_fake_thread_record(user_id=user.user_id))
    thread_repo_mock.create = AsyncMock()

    repo_mock = MagicMock()
    repo_mock.list_for_thread = AsyncMock(return_value=[])
    repo_mock.create = AsyncMock(return_value=_fake_upload_record(filename="passwd"))

    with (
        patch.object(uploads, "get_object_storage", return_value=storage),
        patch("app.gateway.routers.uploads.UploadRepo", return_value=repo_mock),
        patch("app.gateway.routers.uploads.ThreadRepo", return_value=thread_repo_mock),
        patch.object(uploads, "ensure_uploads_dir", return_value=tmp_path),
        patch.object(uploads, "get_sandbox_provider", return_value=provider),
    ):
        for bad_name in ["..", "."]:
            file = UploadFile(filename=bad_name, file=BytesIO(b"data"))
            result = asyncio.run(
                uploads.upload_files(
                    thread_id=str(uuid.uuid4()),
                    files=[file],
                    user=user,
                    db=db,
                )
            )
            assert result.success is True
            assert result.files == []

        file = UploadFile(filename="../etc/passwd", file=BytesIO(b"data"))
        result = asyncio.run(
            uploads.upload_files(
                thread_id=str(uuid.uuid4()),
                files=[file],
                user=user,
                db=db,
            )
        )
        assert result.success is True
        assert len(result.files) == 1
        assert result.files[0]["filename"] == "passwd"


def test_upload_files_reuses_existing_identical_file(tmp_path):
    """Retrying the same file in a thread should be treated as idempotent success."""
    user = _fake_user()
    db = _fake_db()
    storage = _fake_storage()
    provider, sandbox = _sandbox_mocks("local")
    existing_upload = _fake_upload_record(filename="notes.txt", size_bytes=13, bos_key="existing-key")
    storage.get = AsyncMock(return_value=b"hello uploads")

    thread_repo_mock = MagicMock()
    thread_repo_mock.get = AsyncMock(return_value=_fake_thread_record(user_id=user.user_id))
    thread_repo_mock.create = AsyncMock()

    repo_mock = MagicMock()
    repo_mock.list_for_thread = AsyncMock(return_value=[existing_upload])
    repo_mock.create = AsyncMock()

    with (
        patch.object(uploads, "get_object_storage", return_value=storage),
        patch("app.gateway.routers.uploads.UploadRepo", return_value=repo_mock),
        patch("app.gateway.routers.uploads.ThreadRepo", return_value=thread_repo_mock),
        patch.object(uploads, "ensure_uploads_dir", return_value=tmp_path),
        patch.object(uploads, "get_sandbox_provider", return_value=provider),
    ):
        file = UploadFile(filename="notes.txt", file=BytesIO(b"hello uploads"))
        result = asyncio.run(
            uploads.upload_files(
                thread_id=str(uuid.uuid4()),
                files=[file],
                user=user,
                db=db,
            )
        )

    assert result.success is True
    assert result.files[0]["filename"] == "notes.txt"
    assert result.files[0]["reused"] == "true"
    storage.put.assert_not_called()
    repo_mock.create.assert_not_called()
    assert (tmp_path / "notes.txt").read_bytes() == b"hello uploads"


def test_upload_files_renames_when_existing_filename_has_different_content(tmp_path):
    """A conflicting filename with different content should be auto-renamed."""
    user = _fake_user()
    db = _fake_db()
    storage = _fake_storage()
    provider, sandbox = _sandbox_mocks("local")
    existing_upload = _fake_upload_record(filename="notes.txt", size_bytes=8, bos_key="existing-key")
    created_upload = _fake_upload_record(filename="notes_1.txt", size_bytes=13, bos_key="new-key")
    storage.get = AsyncMock(return_value=b"old data")

    thread_repo_mock = MagicMock()
    thread_repo_mock.get = AsyncMock(return_value=_fake_thread_record(user_id=user.user_id))
    thread_repo_mock.create = AsyncMock()

    repo_mock = MagicMock()
    repo_mock.list_for_thread = AsyncMock(return_value=[existing_upload])
    repo_mock.create = AsyncMock(return_value=created_upload)

    with (
        patch.object(uploads, "get_object_storage", return_value=storage),
        patch("app.gateway.routers.uploads.UploadRepo", return_value=repo_mock),
        patch("app.gateway.routers.uploads.ThreadRepo", return_value=thread_repo_mock),
        patch.object(uploads, "ensure_uploads_dir", return_value=tmp_path),
        patch.object(uploads, "get_sandbox_provider", return_value=provider),
    ):
        file = UploadFile(filename="notes.txt", file=BytesIO(b"hello uploads"))
        result = asyncio.run(
            uploads.upload_files(
                thread_id=str(uuid.uuid4()),
                files=[file],
                user=user,
                db=db,
            )
        )

    assert result.success is True
    assert result.files[0]["filename"] == "notes_1.txt"
    repo_mock.create.assert_awaited_once()
    assert repo_mock.create.await_args.kwargs["filename"] == "notes_1.txt"
    storage.put.assert_awaited_once()
    assert (tmp_path / "notes_1.txt").read_bytes() == b"hello uploads"


def test_upload_files_duplicate_names_in_batch_do_not_fail(tmp_path):
    """A duplicate name in the same batch should be renamed instead of failing the request."""
    user = _fake_user()
    db = _fake_db()
    storage = _fake_storage()
    provider, sandbox = _sandbox_mocks("local")
    created_uploads = [
        _fake_upload_record(filename="notes.txt", size_bytes=5, bos_key="k1"),
        _fake_upload_record(filename="notes_1.txt", size_bytes=6, bos_key="k2"),
    ]

    thread_repo_mock = MagicMock()
    thread_repo_mock.get = AsyncMock(return_value=_fake_thread_record(user_id=user.user_id))
    thread_repo_mock.create = AsyncMock()

    repo_mock = MagicMock()
    repo_mock.list_for_thread = AsyncMock(return_value=[])
    repo_mock.create = AsyncMock(side_effect=created_uploads)

    with (
        patch.object(uploads, "get_object_storage", return_value=storage),
        patch("app.gateway.routers.uploads.UploadRepo", return_value=repo_mock),
        patch("app.gateway.routers.uploads.ThreadRepo", return_value=thread_repo_mock),
        patch.object(uploads, "ensure_uploads_dir", return_value=tmp_path),
        patch.object(uploads, "get_sandbox_provider", return_value=provider),
    ):
        result = asyncio.run(
            uploads.upload_files(
                thread_id=str(uuid.uuid4()),
                files=[
                    UploadFile(filename="notes.txt", file=BytesIO(b"first")),
                    UploadFile(filename="notes.txt", file=BytesIO(b"second")),
                ],
                user=user,
                db=db,
            )
        )

    assert result.success is True
    assert [file["filename"] for file in result.files] == ["notes.txt", "notes_1.txt"]
    assert repo_mock.create.await_count == 2


def test_list_uploaded_files_reads_from_pg():
    """List endpoint reads from PG metadata."""
    user = _fake_user()
    db = _fake_db()
    tid = uuid.uuid4()

    records = [
        _fake_upload_record(filename="a.txt", size_bytes=100, bos_key="k1"),
        _fake_upload_record(filename="b.pdf", size_bytes=200, bos_key="k2", markdown_bos_key="k2.md"),
    ]
    repo_mock = MagicMock()
    repo_mock.list_for_thread = AsyncMock(return_value=records)

    with (
        patch("app.gateway.routers.uploads.UploadRepo", return_value=repo_mock),
        patch("app.gateway.routers.uploads._verify_thread_ownership", new_callable=AsyncMock),
    ):
        result = asyncio.run(
            uploads.list_uploaded_files(
                thread_id=str(tid),
                user=user,
                db=db,
            )
        )

    assert result["count"] == 2
    assert result["files"][0]["filename"] == "a.txt"
    assert result["files"][1]["filename"] == "b.pdf"
    assert "markdown_file" not in result["files"][0]
    assert result["files"][1]["markdown_file"] == "b.pdf.extracted.md"


def test_delete_uploaded_file_removes_from_bos_and_pg():
    """Delete removes file from BOS, local dir, and PG."""
    user = _fake_user()
    db = _fake_db()
    tid = uuid.uuid4()
    storage = _fake_storage()

    record = _fake_upload_record(filename="notes.txt", bos_key="k1", markdown_bos_key="k1.md")
    repo_mock = MagicMock()
    repo_mock.get_by_filename = AsyncMock(return_value=record)
    repo_mock.delete = AsyncMock(return_value=True)

    with (
        patch.object(uploads, "get_object_storage", return_value=storage),
        patch("app.gateway.routers.uploads.UploadRepo", return_value=repo_mock),
        patch("app.gateway.routers.uploads._verify_thread_ownership", new_callable=AsyncMock),
        patch("crab.uploads.manager.get_uploads_dir", side_effect=ValueError("no dir")),
    ):
        result = asyncio.run(
            uploads.delete_uploaded_file(
                thread_id=str(tid),
                filename="notes.txt",
                user=user,
                db=db,
            )
        )

    assert result == {"success": True, "message": "Deleted notes.txt"}
    assert storage.delete.call_count == 2
    repo_mock.delete.assert_called_once_with(record.id)
    db.commit.assert_called_once()


def test_delete_file_not_found_returns_404():
    """Delete returns 404 when file not in PG."""
    user = _fake_user()
    db = _fake_db()
    tid = uuid.uuid4()

    repo_mock = MagicMock()
    repo_mock.get_by_filename = AsyncMock(return_value=None)

    with (
        patch("app.gateway.routers.uploads.UploadRepo", return_value=repo_mock),
        patch("app.gateway.routers.uploads._verify_thread_ownership", new_callable=AsyncMock),
    ):
        try:
            asyncio.run(
                uploads.delete_uploaded_file(
                    thread_id=str(tid),
                    filename="nonexistent.txt",
                    user=user,
                    db=db,
                )
            )
            assert False, "Expected HTTPException"
        except Exception as e:
            assert e.status_code == 404


def test_upload_bos_key_format():
    """BOS key follows the expected pattern."""
    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    thread_id = uuid.uuid4()
    upload_id = uuid.uuid4()
    key = uploads._bos_key(tenant_id, user_id, thread_id, upload_id, "notes.txt")
    assert key == f"{tenant_id}/{user_id}/uploads/{thread_id}/{upload_id}_notes.txt"


def test_bos_md_key_appends_suffix():
    """Markdown BOS key is the original key + '.md'."""
    key = uploads._bos_md_key("a/b/c/file.pdf")
    assert key == "a/b/c/file.pdf.md"
