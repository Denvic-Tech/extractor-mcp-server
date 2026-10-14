"""Initialization stays separate from export, with shared uncertain-launch protection."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import sqlite3
from unittest.mock import patch, Mock
import uuid

import pytest
from mcp.server.fastmcp.exceptions import ToolError
from pydantic import ValidationError

from extractor1c import mcp_server as server
from extractor1c.operation_store import OperationStore, OperationIndeterminate
from extractor1c.projects_api import ApiSettings, ProjectsApiClient

GUID = '4eac24a8-18ba-4ad0-a595-2dba2017bf14'
TASK = '2e129faa-5715-4be0-94b7-36833249e5fc'
JOB = '0d217bff-9e86-4200-aa7e-7b37de99d3d4'
ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def store(tmp_path):
    result = OperationStore(tmp_path / 'operations.sqlite3')
    with patch.object(server, 'operation_store', return_value=result):
        yield result


def plan():
    with patch.object(server, 'call_api', return_value={'approved': True, 'expectedProjectState': 'init-state'}) as backend:
        result = server.preview_project_initialization(GUID)
    backend.assert_called_once_with('projects/initialization/preview', {'projectGuid': GUID})
    return result['planId']


def test_initialization_plan_is_immutable_and_cannot_be_exported(store):
    plan_id = plan()
    saved = store.get_plan(plan_id)
    assert saved.action == 'initialization'
    assert saved.payload == {'projectGuid': GUID, 'expectedProjectState': 'init-state'}
    assert server.get_project_plan(plan_id)['action'] == 'initialization'
    with patch.object(server, 'call_api') as backend, pytest.raises(ToolError):
        server.start_project_export(plan_id, str(uuid.uuid4()))
    backend.assert_not_called()


@pytest.mark.parametrize('response', [
    {'launchStatus': 'started', 'taskId': TASK, 'backgroundJobId': JOB},
    {'launchStatus': 'finished', 'taskId': TASK, 'status': 'succeeded'},
    {'launchStatus': 'finished', 'taskId': TASK, 'status': 'failed'},
    {'launchStatus': 'finished', 'taskId': TASK, 'status': 'cancelled'},
    {'launchStatus': 'initializing', 'taskId': TASK},
    {'launchStatus': 'already_running', 'taskId': TASK},
    {'launchStatus': 'rejected'},
])
def test_confirmed_initialization_and_native_sync_fallback_replay(store, response):
    plan_id = plan()
    operation = str(uuid.uuid4())
    with patch.object(server, 'call_api', return_value=response) as backend:
        result = server.initialize_project_queue(plan_id, operation)
        assert result['result'] == response
        assert server.initialize_project_queue(plan_id, operation)['replayed']
    backend.assert_called_once_with('projects/initialization/start', store.get_plan(plan_id).payload)


@pytest.mark.parametrize('response', [None, {}, {'launchStatus': 'unknown'},
    {'launchStatus': 'finished', 'taskId': TASK},
    {'launchStatus': 'finished', 'taskId': TASK, 'status': 'running'},
    {'launchStatus': 'started', 'taskId': TASK}])
def test_uncertain_initialization_blocks_export_and_initialization(store, response):
    plan_id = plan()
    operation = str(uuid.uuid4())
    with patch.object(server, 'call_api', return_value=response), pytest.raises(ToolError, match='indeterminate'):
        server.initialize_project_queue(plan_id, operation)
    assert store.operation_status(operation)['result'] == response
    for action in ('export', 'initialization'):
        next_plan = store.create_plan(action, {'projectGuid': GUID.upper()}, {}, 300)
        with pytest.raises(OperationIndeterminate):
            store.begin(next_plan.plan_id, str(uuid.uuid4()), action)


def test_uncertain_export_blocks_initialization_and_survives_reopen(store):
    old = store.create_plan('export', {'projectGuid': GUID}, {}, 300)
    operation = str(uuid.uuid4())
    store.begin(old.plan_id, operation, 'export')
    store.mark_indeterminate(operation)
    reopened = OperationStore(store.path)
    pending = reopened.create_plan('initialization', {'projectGuid': GUID}, {}, 300)
    with pytest.raises(OperationIndeterminate):
        reopened.begin(pending.plan_id, str(uuid.uuid4()), 'initialization')


def test_distinct_export_and_initialization_plans_serialize(store):
    plans = [store.create_plan(action, {'projectGuid': GUID}, {}, 300) for action in ('export', 'initialization')]
    def begin(item):
        try:
            store.begin(item.plan_id, str(uuid.uuid4()), item.action)
            return 'started'
        except OperationIndeterminate:
            return 'blocked'
    with ThreadPoolExecutor(max_workers=2) as executor:
        assert sorted(executor.map(begin, plans)) == ['blocked', 'started']


def test_legacy_export_store_migrates_and_preserves_uncertain_block(tmp_path):
    path = tmp_path / 'legacy.sqlite3'
    old = OperationStore(path)
    saved = old.create_plan('export', {'projectGuid': GUID}, {}, 300)
    operation = str(uuid.uuid4())
    old.begin(saved.plan_id, operation, 'export')
    old.mark_indeterminate(operation)
    with sqlite3.connect(path) as connection:
        for table in ('plans', 'operations'):
            schema = connection.execute('SELECT sql FROM sqlite_master WHERE name=?', (table,)).fetchone()[0]
            schema = schema.replace(", 'initialization'", '')
            connection.execute(f'CREATE TEMP TABLE {table}_backup AS SELECT * FROM {table}')
            connection.execute(f'DROP TABLE {table}')
            connection.execute(schema)
            connection.execute(f'INSERT INTO {table} SELECT * FROM {table}_backup')
    migrated = OperationStore(path)
    added = migrated.create_plan('initialization', {'projectGuid': GUID}, {}, 300)
    with pytest.raises(OperationIndeterminate):
        migrated.begin(added.plan_id, str(uuid.uuid4()), 'initialization')
    with sqlite3.connect(path) as connection:
        assert not connection.execute('PRAGMA foreign_key_check').fetchall()


def test_api_initialization_uses_closed_fixed_post_contract():
    session = Mock()
    session.post.return_value = Mock(status_code=200, headers={})
    session.post.return_value.json.return_value = {'launchStatus': 'rejected'}
    client = ProjectsApiClient(ApiSettings(_env_file=None, url='https://example.test/base/hs/extractor-projects/v1',
                                          user='test', password='test'), session)
    with pytest.raises(ValidationError):
        client.call('projects/initialization/start', {'projectGuid': GUID, 'code': 'arbitrary'})
    session.post.assert_not_called()
    client.call('projects/initialization/start', {'projectGuid': GUID, 'expectedProjectState': 'init-state'})
    assert session.post.call_args.args[0].endswith('/projects/initialization/start')


@pytest.mark.parametrize('preview', [{'approved': False}, {'approved': 'true'}])
def test_blocked_initialization_cannot_issue_plan(store, preview):
    with patch.object(server, 'call_api', return_value=preview):
        assert server.preview_project_initialization(GUID)['planId'] is None
