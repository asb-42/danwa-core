"""ProjectManager unit tests.

The legacy second ``DMS`` class (``backend/services/dms/dms.py``) and its
``DMSMemory`` facade were removed (review §3.3, 2026-08-31): the class was
constructor-incompatible with the real ``DMS`` (dict passed where a Path is
required, ``DMSDB()`` with no path, ``asyncio.run`` inside async routes)
and had no production callers. The live ``DMS`` service is covered by the
danwa-core native ``test_dms_*.py`` suites; this file keeps the
``ProjectManager`` tests that still apply.
"""
import pytest
from unittest.mock import Mock

from backend.services.dms.project_manager import ProjectManager


class TestProjectManager:
    @pytest.fixture(autouse=True)
    def setup(self):
        self.mock_db = Mock()
        self.pm = ProjectManager(self.mock_db)
        yield

    def test_project_crud_operations(self):
        mock_project = {"id": "proj1", "name": "Test", "description": "Desc", "created_at": "2024-01-01"}
        self.mock_db.create_project.return_value = mock_project
        result = self.pm.create_project("Test", "Desc")
        assert result == mock_project
        self.mock_db.create_project.assert_called_once_with("Test", "Desc")

        self.mock_db.get_project.return_value = mock_project
        result = self.pm.get_project("proj1")
        assert result == mock_project
        self.mock_db.get_project.assert_called_once_with("proj1")

        self.mock_db.get_project.return_value = None
        result = self.pm.get_project("invalid")
        assert result is None

        mock_projects = [mock_project]
        self.mock_db.list_projects.return_value = mock_projects
        result = self.pm.list_projects()
        assert result == mock_projects

        self.mock_db.get_project.return_value = mock_project
        updated_project = {**mock_project, "name": "Updated"}
        self.mock_db.get_project.return_value = updated_project
        result = self.pm.update_project("proj1", name="Updated")
        assert result["name"] == "Updated"
        assert self.mock_db.execute.called
        self.mock_db.commit.assert_called_once()

        self.mock_db.get_project.return_value = mock_project
        result = self.pm.update_project("proj1")
        assert result == mock_project

        self.mock_db.get_project.return_value = None
        result = self.pm.update_project("invalid", name="Test")
        assert result is None

        self.mock_db.delete_project.return_value = True
        result = self.pm.delete_project("proj1")
        assert result is True
        self.mock_db.delete_project.assert_called_once_with("proj1")

    def test_document_management(self):
        assert not hasattr(self.pm, "add_document")
        assert not hasattr(self.pm, "delete_document")
        assert not hasattr(self.pm, "list_documents")
