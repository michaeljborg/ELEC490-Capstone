import socket
import json

HOST = "0.0.0.0"
PORT = 5000

with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
    s.bind((HOST, PORT))
    s.listen()

    print(f"Listening on port {PORT}...")

    while True:
        conn, addr = s.accept()
        with conn:
            data = conn.recv(4096)
            if data:
                try:
                    metrics = json.loads(data.decode("utf-8"))
                    print(metrics)
                except:
                    pass
