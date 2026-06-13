import socket
import sys
import time
import unittest

class TestPortReuseMechanism(unittest.TestCase):
    def test_reuseaddr_prevents_time_wait_errors(self):
        if sys.platform == "win32":
            raise unittest.SkipTest("SO_REUSEADDR behavior in TIME_WAIT is platform-dependent and does not restrict bind on Windows.")
        # We will use an ephemeral port for testing
        test_port = 28193

        # Step 1: Bind a server socket, establish a connection, and close it
        # This puts the socket/port into TIME_WAIT state because data is exchanged
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind(("127.0.0.1", test_port))
        server.listen(1)

        client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        client.connect(("127.0.0.1", test_port))

        conn, addr = server.accept()
        
        # Send some data to ensure the TCP connection is fully active
        conn.sendall(b"test")
        client.recv(4)

        # Close all sockets in the sequence that leaves the port in TIME_WAIT
        # The side initiating the active close (conn/server side) goes into TIME_WAIT
        conn.close()
        client.close()
        server.close()

        # Step 2: Attempting to bind a new socket WITHOUT SO_REUSEADDR should fail
        # because the port is in TIME_WAIT state.
        probe_without_reuse = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            probe_without_reuse.bind(("127.0.0.1", test_port))
            bind_succeeded = True
        except OSError as e:
            bind_succeeded = False
            print(f"\n[Expected behavior] Bind without SO_REUSEADDR failed as expected: {e}")
        finally:
            probe_without_reuse.close()

        self.assertFalse(bind_succeeded, "Socket bind without SO_REUSEADDR should have failed during TIME_WAIT!")

        # Step 3: Attempting to bind a new socket WITH SO_REUSEADDR should succeed
        probe_with_reuse = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        probe_with_reuse.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            probe_with_reuse.bind(("127.0.0.1", test_port))
            reuse_succeeded = True
        except OSError as e:
            reuse_succeeded = False
            print(f"\n[Unexpected behavior] Bind with SO_REUSEADDR failed: {e}")
        finally:
            probe_with_reuse.close()

        self.assertTrue(reuse_succeeded, "Socket bind with SO_REUSEADDR should have succeeded during TIME_WAIT!")
        print("[Expected behavior] Bind with SO_REUSEADDR succeeded perfectly.")

if __name__ == "__main__":
    unittest.main()
