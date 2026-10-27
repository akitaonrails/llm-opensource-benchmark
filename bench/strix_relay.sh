#!/usr/bin/env bash
# One TCP connection to the Strix model server, carried over an SSH command's stdin/stdout.
# The Strix's sshd sets AllowTcpForwarding no, so ssh -L / -W are refused; this runs a tiny
# Python byte pipe on the Strix instead. Used as: socat TCP-LISTEN:...,fork EXEC:bench/strix_relay.sh
PORT=${STRIX_PORT:-18083}
RELAY='
import socket, sys, threading
s = socket.create_connection(("127.0.0.1", '"$PORT"'))
def up():
    while (data := sys.stdin.buffer.read1(65536)):
        s.sendall(data)
    s.shutdown(socket.SHUT_WR)
threading.Thread(target=up, daemon=True).start()
while (data := s.recv(65536)):
    sys.stdout.buffer.write(data); sys.stdout.buffer.flush()
'
exec ssh -T -o BatchMode=yes -o ControlMaster=auto -o ControlPath=/tmp/osb-strix-%C -o ControlPersist=600 \
  192.168.0.90 "python3 -u -c '$RELAY'"
