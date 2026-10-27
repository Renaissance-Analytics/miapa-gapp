"""Real finite fixture request lifetime; database cleanup belongs to its owning thread."""
import threading
import time
import unittest
from unittest.mock import patch
import urllib.request

from django.db import connections
from tools.check_browser import ThreadedFixtureServer, QuietHandler
from wsgiref.simple_server import make_server


class BrowserServerTests(unittest.TestCase):
    def test_inflight_request_is_bounded_and_closes_its_own_database_before_fixture_removal(self):
        entered, release = threading.Event(), threading.Event()
        closed = []
        original = connections.close_all
        def close_owned():
            original()
            closed.append(threading.get_ident())
        request_owner = []
        def application(environ, start_response):
            request_owner.append(threading.get_ident())
            with connections['default'].cursor() as cursor:
                cursor.execute('SELECT 1')
            entered.set()
            if not release.wait(3): raise ValueError('Finite fixture release missing')
            start_response('200 OK', [('Content-Length', '2')])
            return [b'ok']
        with patch.object(connections, 'close_all', side_effect=close_owned):
            server = make_server('127.0.0.1', 0, application, server_class=ThreadedFixtureServer, handler_class=QuietHandler)
            serving = threading.Thread(target=server.serve_forever, daemon=True)
            serving.start()
            outcome = []
            def request():
                with urllib.request.urlopen(f'http://127.0.0.1:{server.server_port}', timeout=4) as response:
                    outcome.append(response.read())
            reader = threading.Thread(target=request, daemon=True)
            reader.start()
            try:
                self.assertTrue(entered.wait(2))
                server.shutdown()
                server.server_close()
                self.assertFalse(server.wait_for_requests(deadline=time.monotonic() + .05))
                self.assertNotIn(request_owner[0], closed)
                release.set()
                self.assertTrue(server.wait_for_requests(deadline=time.monotonic() + 2))
                reader.join(timeout=2)
                self.assertEqual(outcome, [b'ok'])
                self.assertIn(request_owner[0], closed)
            finally:
                release.set()
                reader.join(timeout=4)
                server.shutdown(); server.server_close()
                serving.join(timeout=2)
            self.assertFalse(serving.is_alive())
            self.assertFalse(reader.is_alive())
