"""Wait for the existing server to bind after its actual startup work finishes."""
import socket
import sys
import time

while True:
    try:
        connection = socket.create_connection(('127.0.0.1', int(sys.argv[1])), timeout=1)
    except ConnectionRefusedError:
        time.sleep(0.2)
        continue
    connection.close()
    break
