"""Real PostgreSQL regression tests; only use a dedicated test database.

Set TEST_POSTGRES_URL to enable. Each test owns a unique, disposable schema.
Never reads DATABASE_URL from the deployed application environment.
"""
import os
import unittest
import uuid
from unittest.mock import patch

import app as petstock
from test_workflow import InventoryWorkflowTests as WorkflowTests


@unittest.skipUnless(os.environ.get('TEST_POSTGRES_URL'), 'TEST_POSTGRES_URL not configured')
class PostgreSQLWorkflowTests(WorkflowTests):
    def setUp(self):
        import psycopg
        from psycopg import sql
        from psycopg.rows import dict_row

        url = os.environ['TEST_POSTGRES_URL']
        schema = 'petstock_test_' + uuid.uuid4().hex
        admin = psycopg.connect(url, autocommit=True)
        self.addCleanup(admin.close)
        admin.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(schema)))
        self.addCleanup(lambda: admin.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(schema))))

        def connect():
            raw = psycopg.connect(url, autocommit=True, row_factory=dict_row)
            raw.execute(sql.SQL('SET search_path TO {}').format(sql.Identifier(schema)))
            return petstock.PostgresConnection(raw)

        for patcher in (patch.object(petstock, 'USING_POSTGRES', True),
                        patch.object(petstock, 'open_connection', connect),
                        patch.object(petstock, '_database_initialized', False)):
            patcher.start()
            self.addCleanup(patcher.stop)
        petstock.app.config.update(TESTING=True, SECRET_KEY='test')
        petstock.init_database()
        self.client = petstock.app.test_client()
        self.client.get('/login')

    def tearDown(self):
        pass  # addCleanup restores globals and drops only this test's schema.


# Avoid unittest discovering the imported base class a second time.
del WorkflowTests
